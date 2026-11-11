import logging
import os
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

from .config import load_config, warn_default_allowed_models_applied, warn_deprecated_allowed_models_env, warn_deprecated_reasoning_echo_env

# ── Allocator cap (resource-cost-control O1, 2026-10-18) ──────────────────
# Belt-and-braces env default. Scope, verified against the glibc tunables
# model and this repo's actual boot paths:
#
#   CANNOT cap THIS process's own glibc arenas. MALLOC_ARENA_MAX is a libc
#     tunable parsed at libc init — before main()/the interpreter starts —
#     so an in-process setdefault is always too late for the parent. The
#     parent-side cap comes from the pre-exec surfaces instead: the systemd
#     unit template (scripts/systemd/ensemble-daemon.service) and
#     launcher.sh main(), which both set the env BEFORE process start.
#
#   CAN reach child processes that inherit os.environ — the meaningful lane
#     is bash/proc tool children (daemon/tools/bash.py spawns with
#     env=None → inherited environ, or os.environ.copy() + overlay), i.e.
#     agent-driven node/npm/test-farm processes pick the cap up.
#
#   CAN cover boot paths that bypass launcher.sh: the frozen PyInstaller
#     entry (run_app.py imports this module before anything spawns) and
#     `python -m daemon`. NOT the dev.sh `python -m uvicorn daemon.api:app`
#     path (never imports this module), and NOT MCP stdio children
#     (daemon/mcp/connection_manager.py builds their env fresh from the
#     stored server config via resolve_env — os.environ is deliberately
#     never merged there). Both gaps are known O1 residuals, out of scope.
#
# Module level = runs at import, before uvicorn/threads/any child spawn on
# every path that reaches it. setdefault: a staged MALLOC_ARENA_MAX wins.
os.environ.setdefault("MALLOC_ARENA_MAX", "2")

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
#         OAuth2 Phase 1 (AMEND v1.1 / C4) adds the auth-CONFIG
#         misconfig half to the same code: auth.enabled=true with a
#         misconfigured ENSEMBLE_AUTH_* section exits 78 at the
#         preflight, BEFORE the port bind (see
#         _boot_auth_config_preflight).
#   1   = crash (unhandled exception). Restart with backoff. The
#         default crash path; no explicit code needed.
# ───────────────────────────────────────────────────────────────────────────

# Exit code for boot-time PG unreachability (EX_TEMPFAIL — see block above).
_EXIT_BOOT_DB_TEMPFAIL = 75

# Exit code for boot-time PG auth refusal (EX_CONFIG — see block above):
# wrong credentials can never be fixed by retrying, so the launcher
# must stop instead of looping on the budget-exempt 75 track.
_EXIT_BOOT_DB_AUTH_REFUSED = 78

# OAuth2 Phase 1 (AMEND v1.1 / C4): the auth-CONFIG misconfig lane shares
# the same EX_CONFIG contract — a misconfigured ENSEMBLE_AUTH_* section
# cannot be fixed by restarting either. Alias keeps the call sites
# self-documenting without forking the numeric contract.
_EXIT_BOOT_AUTH_CONFIG_REFUSED = _EXIT_BOOT_DB_AUTH_REFUSED

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
    Duplicated here because the preflight builds its OWN probe engine.
    Both engines now carry the SAME ``BOOT_DB_TIMEOUT_S`` bound, but
    by DIFFERENT mechanisms: the shared factory rides it on the URL
    query (``?connect_timeout=…``), while the preflight passes it via
    ``connect_args`` on its locally-built engine — the factory's
    ``connect_args`` slot is reserved for the B2/B3 UTC-session
    contract and must not change. Keep this URL construction in sync
    with the factory's if the factory's URL construction changes.

    The CONSTRUCTION is now the same ``URL.create(...)`` the factory
    uses (S1 close, 2026-10-21): the f-string form put the RAW
    password into the URL, so a password containing ``@`` re-parsed
    into a DIFFERENT (unresolvable) host and boot-blocked the daemon
    with exit 75. The BOUND still rides ``connect_args`` here (not on
    the URL query) — that mechanism difference is deliberate and
    unchanged, so the rendered URL stays byte-for-byte identical to the
    pre-change string form in the normal case.
    """
    import os

    from sqlalchemy.engine import URL

    pg = ensemble_config.postgres
    host = os.environ.get("POSTGRES_HOST", pg.host)
    port = os.environ.get("POSTGRES_PORT", str(pg.port))
    db = os.environ.get("POSTGRES_DB", pg.db)
    user = os.environ.get("POSTGRES_USER", pg.user)
    password = os.environ.get("POSTGRES_PASSWORD", pg.password)
    # URL.create(...) — NOT an f-string (same construction as
    # daemon/repositories/factory.py::create_postgres_engine). The
    # string form put the RAW password into the URL: a password
    # containing '@' re-parsed into a DIFFERENT host (unresolvable ->
    # exit 75, fail-closed) and the DATABASE name was never the
    # corrupted part. URL.create escapes the password, so the host,
    # port and database stay the intended ones. The libpq
    # connect_timeout is deliberately NOT repeated on the URL query:
    # the preflight engine carries it via connect_args (the mechanism
    # the factory does not use), and the factory's connect_args slot
    # is reserved for the B2/B3 UTC-session contract.
    return URL.create(
        drivername="postgresql+psycopg",
        username=user,
        password=password,
        host=host,
        port=port,
        database=db,
    ).render_as_string(hide_password=False)


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
    probe pins it through ``connect_args`` (the shared factory rides
    the SAME bound on its URL query), so a firewall-DROP host fails
    inside the budget instead of hanging ~75-130s:

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
        # shared factory carries the SAME bound, but on its URL query
        # — the preflight's ``connect_args`` slot is its own (the
        # factory's slot is the B2/B3 UTC-session contract). The
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


def _boot_auth_config_preflight() -> None:
    """Boot preflight: OAuth2 auth-config misconfig enumeration (Phase 1, C4).

    AMEND v1.1 / C4 siting: the exit-78 misconfig lane lives at the BOOT
    PREFLIGHT — BEFORE ``uvicorn.run`` (pre-port-bind) — never at
    lifespan (a lifespan RuntimeError exits via uvicorn's generic path:
    exit 1, post-port-bind; NFR-7's code-78 contract would be
    unreachable there). Discovery/JWKS reachability is deliberately NOT
    checked here: that is network I/O and stays a lifespan WARN (P2+).

    Static only: the single helper
    :func:`daemon.config.auth_config_boot_refusals` loads the config
    (yaml + env, no network) and returns operator-readable refusal lines
    — each naming the offending ``ENSEMBLE_AUTH_*`` env var exactly — or
    an empty list. It is also the SINGLE owner of the auth-refusal exit
    path (phase1-plan §6 risk 3): later phases call the helper, never a
    second ``sys.exit`` site.

    Ordering note: this runs as the FIRST statement of ``main()``,
    before ``load_config()`` — the config-load itself is the raising
    step for an auth misconfig, so a lane placed after it (beside the
    DB probe call site) would be unreachable for every
    validator-refused configuration. Fail-open posture mirrors
    ``_boot_db_preflight``: a broken check (non-auth config error,
    import failure) logs and proceeds — the normal load_config path
    surfaces unrelated config errors with its own traceback.
    OFF state: the helper returns ``[]`` and this function is fully
    silent — zero auth log lines (G1.3).
    """
    try:
        from .config import auth_config_boot_refusals

        refusals = auth_config_boot_refusals()
    except SystemExit:
        raise
    except Exception as exc:
        logger.warning(
            "Boot preflight: auth config check skipped "
            "(fail-open, proceeding): %s",
            exc,
        )
        return
    if not refusals:
        return
    for line in refusals:
        logger.error("Auth config misconfig: %s", line)
    logger.error(
        "  → auth misconfiguration at boot: fix the ENSEMBLE_AUTH_* "
        "settings (or set ENSEMBLE_AUTH_ENABLED=false to disable auth) "
        "and restart\n"
        "  → exiting %d (EX_CONFIG); launcher must NOT retry — "
        "restarting cannot fix a config refusal",
        _EXIT_BOOT_AUTH_CONFIG_REFUSED,
    )
    sys.exit(_EXIT_BOOT_AUTH_CONFIG_REFUSED)


class _StartedLogServer(uvicorn.Server):
    """``uvicorn.Server`` that emits the 'Uvicorn running on ...' line.

    uvicorn logs that line from ``Server.startup()`` — but ONLY when it
    bound the socket itself. With a socket handed in, it SKIPS the line
    (uvicorn/server.py:188-193: "a message has already been logged by
    ``config.bind_socket()``"). We bind the socket ourselves, so we own
    the line.

    It is emitted at the END of ``startup()`` — i.e. AFTER the app
    import (which installs the root log handlers) and after the
    lifespan startup — so it lands in the same place, in the same
    format, as uvicorn's own line does today. Only the PUBLIC
    ``Server.startup`` / ``Server.started`` / ``Server.config`` surface
    is used; nothing private.

    (A line logged BEFORE the app import is silently DROPPED: the root
    logger has no handler until ``daemon.api`` is imported, and
    ``logging.lastResort`` only emits WARNING+. The pre-bind path
    therefore installs the handlers itself — see
    ``_serve_with_bind_retry``.)
    """

    def __init__(self, config, bound_socket=None):
        super().__init__(config)
        self._bound_socket = bound_socket

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if not self.started:
            return
        host = self.config.host or "0.0.0.0"
        port = self.config.port
        if self._bound_socket is not None:
            try:
                port = self._bound_socket.getsockname()[1]
            except OSError:  # pragma: no cover — socket already closed
                pass
        logger.info(
            "Uvicorn running on http://%s:%s (Press CTRL+C to quit)",
            host,
            port,
        )


def _serve_with_bind_retry(uvicorn_config, max_wait_s: float | None = None) -> None:
    """Bind the listener socket (bounded retry) and serve.

    Mirrors ``uvicorn.run()`` for the kwargs the daemon uses, with ONE
    deliberate difference: the socket is bound HERE — through the
    bounded EADDRINUSE retry in :mod:`daemon.services.bind_retry` — and
    HANDED to uvicorn, so uvicorn never re-binds and the port is never
    unprotected between the bind and the serve.

    uvicorn's own socket path (``Server.startup`` →
    ``loop.create_server(host=..., port=...)``) runs AFTER the lifespan
    startup and, on OSError, does ``logger.error(exc)`` →
    ``await self.lifespan.shutdown()`` → ``sys.exit(1)``
    (uvicorn/server.py:169-186) — the crash exit that produced the
    2026-11-11 restart loop. We never take that path: uvicorn receives
    the socket (``Server.startup`` → ``loop.create_server(...,
    sock=sock)``, uvicorn/server.py:140-146), which listens on the
    socket we already hold.

    Because uvicorn SKIPS its own "Uvicorn running on ..." line when
    sockets are supplied (uvicorn/server.py:188-193), that line is
    emitted by :class:`_StartedLogServer` instead — at the same point
    in the boot sequence as uvicorn's own.

    LOGGING: the root log handlers are installed by the ``daemon.api``
    import — which happens INSIDE uvicorn's startup, i.e. AFTER this
    function's pre-bind phase. Until then the root logger has no
    handler and ``logging.lastResort`` only emits WARNING+, so an
    INFO line emitted by the pre-bind path would be silently DROPPED.
    ``install_root_logging()`` (the app's OWN idempotent installer) is
    therefore called here, BEFORE the bind, so the bind-retry
    diagnostics land in the same sinks as every other daemon log line
    (stderr + ensemble.log) instead of only in the bare lastResort
    stream. The app's own install at import time replaces the tagged
    handler pair, so the final handler set is exactly the same as
    before this change.

    The tail mirrors ``uvicorn.run()`` exactly (uvicorn/main.py:598-614):
    the KeyboardInterrupt swallow, the uds cleanup, and the
    STARTUP_FAILURE (exit 3) verdict when the server never started. The
    exit-3 tail is load-bearing: without it a lifespan-startup failure
    would exit 0 ("clean stop") and the launcher would NOT restart the
    daemon.
    """
    from uvicorn.main import STARTUP_FAILURE

    from .logging_setup import install_root_logging
    from .services.bind_retry import bind_socket_with_retry

    host = uvicorn_config.host
    port = uvicorn_config.port

    # Install the root log handlers BEFORE the pre-bind phase so the
    # bind-retry diagnostics are observable in the normal sinks (see
    # the docstring). Idempotent — the app's own install at import time
    # replaces this tagged handler pair.
    install_root_logging()

    # Bind FIRST, serve second — the socket stays held across the
    # lifespan startup, so there is no window in which the port can be
    # SERVED by another process (only one process can ``listen()``; a
    # second ``bind()`` is still possible under SO_REUSEADDR and loses at
    # ``create_server`` → exit 1 → crash track → retry).
    #
    # SIGINT during the bind WAIT: exit 3 (STARTUP_FAILURE), i.e. the
    # CRASH track — deliberately NOT a clean exit. uvicorn.run swallowed
    # KeyboardInterrupt and its tail then classified a never-started
    # server as STARTUP_FAILURE (uvicorn/main.py:611-614), so the
    # launcher restarted the daemon. A bare ``return`` here would exit 0
    # ("clean"), classify_exit would log "exit 0, not looping", and
    # systemd Restart=on-failure would not fire — a daemon silently down
    # while every supervisor layer believes it stopped cleanly. The wait
    # window is the whole bind wait (up to the budget), so the shape
    # matters. (uvicorn installs its own SIGINT handler only INSIDE
    # ``server.run()``'s ``capture_signals`` — i.e. AFTER this pre-bind
    # phase.)
    #
    # SIGTERM during that window is NOT handler-graceful: pre-fix,
    # uvicorn's ``capture_signals`` was installed before its own bind, so
    # a TERM during the bind was handled; now the window is handler-free
    # and has GROWN from ~0 to <= the budget. Operationally equivalent
    # (TERM → default disposition → prompt death → 143 → crash track →
    # retry); stated here because it is a real change, not a parity.
    try:
        sock = bind_socket_with_retry(host, port, max_wait_s=max_wait_s)
    except KeyboardInterrupt:
        logger.info(
            "Boot interrupted during the listener bind wait — exiting as a "
            "startup failure so the supervisor restarts the daemon"
        )
        sys.exit(STARTUP_FAILURE)

    server = _StartedLogServer(config=uvicorn_config, bound_socket=sock)
    try:
        server.run(sockets=[sock])
    except KeyboardInterrupt:
        pass
    finally:
        if uvicorn_config.uds and os.path.exists(uvicorn_config.uds):
            os.remove(uvicorn_config.uds)

    if (
        not server.started
        and not uvicorn_config.should_reload
        and uvicorn_config.workers == 1
    ):
        # uvicorn closes hand-held sockets only in ``shutdown()`` — which
        # it never reaches when the lifespan failed (``started`` is
        # False), so close the held socket here instead of relying on
        # process exit to release the port.
        try:
            sock.close()
        except OSError:  # pragma: no cover — already closed
            pass
        sys.exit(STARTUP_FAILURE)


def main(run_preflight: bool = True):
    """Main entry point.

    ``run_preflight=False`` is used ONLY by the frozen entry
    (``run_app.py``), which runs ``_boot_db_preflight()`` itself —
    earlier, before this function's config steps — so the probe fires
    exactly once per boot on every entry (F-DR1-1, P2.3 B5.6).
    """
    # Auth-config preflight (OAuth2 Phase 1, AMEND v1.1 / C4): static
    # misconfig enumeration + exit 78 BEFORE any config-driven wiring,
    # the DB probe, or uvicorn.run (pre-port-bind). Runs on EVERY entry
    # (it is static — unlike the DB probe there is no once-per-entry
    # budget concern). See _boot_auth_config_preflight for why this
    # must precede load_config().
    _boot_auth_config_preflight()

    # Boot-time stale-subprocess sweep (resource-cost-control O8a,
    # 2026-10-18). Reaps children orphaned by PRIOR daemon incarnations
    # inside this unit's cgroup — the test-farm leak class from the
    # 2026-10-17 OOM night (a child survived even systemd's cgroup-wide
    # SIGKILL sweep: journal "Unit process 3393684 (MainThread) remains
    # running after unit stopped"). Predicate is fail-closed (own-cgroup
    # membership + starttime older than this process + never self /
    # ancestors / descendants / kernel threads / zombies) and every
    # signal passes the PID-recycle guard (starttime field-22 + pgid +
    # cgroup re-check) immediately beforehand. Default is lane-
    # conditional (remediation r2, 2026-10-18): prod = ON (the promoted
    # frozen PyInstaller binary running under systemd — ``sys.frozen``
    # truthy), dev/test/manual = OFF (source-mode ``python -m daemon``
    # / launcher.sh source runs / every ``pytest`` — ``sys.frozen``
    # falsy). Override is bidirectional in BOTH lanes via
    # ``ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES`` (any non-disable value
    # forces ON; ``0``/``false``/``no``/``off`` forces OFF). Default
    # honors the leader ruling of "ships default-ON at promote"; the
    # source-mode default-OFF avoids collateral-kill breadth in the
    # login-session-scope cgroup and ``main()``-driving tests that
    # never patch the kill-switch env (see W1/W2 review). TERM →
    # bounded grace → SIGKILL, every action logged. Runs here — before
    # config load, uvicorn, and any child-spawning subsystem — so the
    # daemon's own tree is empty and the ancestry exclusion carries
    # the launcher + PyInstaller bootloader. Fail-open: a sweep bug
    # must never block boot. Full mechanism + census evidence: module
    # docstring and
    # docs/investigations/2026-10-18-o8-stale-subprocess-sweep-and-killmode-audit.md
    try:
        from .services.boot_subprocess_sweep import sweep_stale_subprocesses

        sweep_stale_subprocesses()
    except SystemExit:
        raise
    except Exception as exc:  # fail-open belt around the module's own fail-open
        logger.warning(
            "Boot stale-subprocess sweep: skipped (fail-open): %s: %s",
            type(exc).__name__, exc,
        )

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
    # Wire the OPENAI_STREAMING operator knob (CF-125s 524 fix). The
    # chokepoint ``clean_llm_config`` reads this class var when callers
    # don't pass an explicit streaming flag — see daemon/graph.py.
    ThinkingChatOpenAI.default_streaming = bool(config.llm.streaming)
    logger.info(
        f"[Config] default_streaming={ThinkingChatOpenAI.default_streaming} "
        f"(clean_llm_config injects streaming={ThinkingChatOpenAI.default_streaming} "
        f"when callers don't pass an explicit flag; OPENAI_STREAMING env var controls it)"
    )
    # Wire the OPENAI_REQUEST_GZIP operator knob. When True,
    # ``clean_llm_config`` attaches gzip-enabled httpx clients
    # (``http_client`` / ``http_async_client``) to the LangChain
    # ChatOpenAI constructor so every outbound LLM request body is
    # gzip-compressed on the wire. When False (default) no custom
    # clients are attached and the code path is byte-identical to
    # the pre-feature state. See daemon/services/llm_gzip.py for
    # the transport wrapper.
    ThinkingChatOpenAI.default_request_gzip = bool(config.llm.request_gzip)
    logger.info(
        f"[Config] default_request_gzip={ThinkingChatOpenAI.default_request_gzip} "
        f"(clean_llm_config attaches gzip httpx clients when True; "
        f"OPENAI_REQUEST_GZIP env var controls it)"
    )
    # Wire the L0 request-timeout inject-if-absent default
    # (llm-stream-stall-hardening). ``clean_llm_config`` reads this
    # ClassVar when a construction site omits ``request_timeout`` —
    # those sites previously had NO read deadline at all (∞: langchain
    # always passes timeout explicitly, so the SDK's 600s default
    # fallback is unreachable), so the inject is a pure tightening
    # ∞→610s. See daemon/graph.py.
    ThinkingChatOpenAI.default_request_timeout = int(
        config.llm.request_timeout
    )
    logger.info(
        f"[Config] default_request_timeout={ThinkingChatOpenAI.default_request_timeout}s "
        f"(clean_llm_config injects it when a site omits request_timeout "
        f"— tightening ∞→{ThinkingChatOpenAI.default_request_timeout}s; "
        f"OPENAI_REQUEST_TIMEOUT controls it)"
    )

    # Wire the S5 degenerate re-invoke cap (empty-response-guard Phase 1).
    # The router reads this module global on every routing decision —
    # installed ONCE at boot from LimitsConfig
    # (yaml ``limits.empty_degenerate_reinvoke_cap`` /
    # env ``LIMITS_EMPTY_DEGENERATE_REINVOKE_CAP``); restart-required.
    import daemon.graph as _graph
    _graph.EMPTY_DEGENERATE_REINVOKE_CAP = int(
        config.limits.empty_degenerate_reinvoke_cap
    )
    logger.info(
        f"[Config] empty_degenerate_reinvoke_cap={_graph.EMPTY_DEGENERATE_REINVOKE_CAP} "
        f"(router stops re-invoking on reasoning-only/<think>-only empties at the cap)"
    )

    # Wire the b3 completion-gate run-deny cap (2026-10-01, incident
    # 6f961c43). The gate node + its router read this module global on
    # every evaluation — installed ONCE at boot from LimitsConfig
    # (yaml ``limits.attestation_run_deny_cap`` /
    # env ``LIMITS_ATTESTATION_RUN_DENY_CAP``); restart-required.
    _graph.ATTESTATION_RUN_DENY_CAP = int(config.limits.attestation_run_deny_cap)
    logger.info(
        f"[Config] attestation_run_deny_cap={_graph.ATTESTATION_RUN_DENY_CAP} "
        f"(completion gate force-terminalizes LOUD at the per-mission "
        f"deny-continuation cap — recursion_limit is never the only terminator)"
    )

    # Warn-once if the removed allowlist env var is still set (no-op when
    # load_config already emitted it)
    warn_deprecated_reasoning_echo_env()

    # Warn-once if the legacy OPENAI_ALLOWED_MODELS env var is the
    # effective source (no-op when load_config already emitted it).
    warn_deprecated_allowed_models_env()

    # Warn-once when the documented default applies — neither env var
    # set (sel-default-20260927). The module-level guard makes this a
    # no-op when load_config already emitted it; firing here means a
    # process that only goes through the startup path (no prior
    # load_config, rare) still gets the WARNING exactly once.
    warn_default_allowed_models_applied()

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
    #
    # ── Bind-race fix (2026-11-11) ──────────────────────────────────────
    # The server is no longer started through bare ``uvicorn.run(...)``:
    # the listener socket is pre-bound HERE (bounded EADDRINUSE retry,
    # see daemon/services/bind_retry.py) and handed to uvicorn via
    # ``Server.run(sockets=[sock])``. uvicorn's own bind happens INSIDE
    # ``Server.startup`` — AFTER the lifespan startup — and its OSError
    # handler is ``sys.exit(1)`` (uvicorn/server.py:179-182); that
    # crash-exit-on-contention is what produced the 2026-11-11 restart
    # loop (35 respawns). The pre-bind also CLOSES the TOCTOU window:
    # the port is held from before the lifespan startup until serving.
    #
    # ADR-STYLE NOTE — why THIS lane retries and the preflights above do
    # NOT: the preflights (exit 75 PG-unreachable / exit 78 auth
    # refusal) are CONFIG refusals — deterministic; waiting cannot fix a
    # wrong password or an unreachable host, so ADR-011 keeps them "one
    # check, one verdict". The bind is TRANSIENT RESOURCE CONTENTION —
    # the holder is a LIVE process that is on its way out (a dying
    # duplicate/rogue daemon releases the listener in ~1s, because
    # uvicorn closes it FIRST), so waiting is right. The two lanes are
    # deliberately different; do NOT harmonise them.
    #
    # Placement: AFTER both preflights (a config refusal must NEVER bind
    # the port) and IMMEDIATELY BEFORE the server start. The pre-bind
    # lives INSIDE ``main()`` but OUTSIDE the ``if run_preflight:``
    # block, so the FROZEN entry (run_app.py → main(run_preflight=False))
    # is covered by the same fix.
    #
    # ORDERING (verified, not assumed): the pre-bind happens BEFORE
    # uvicorn's lifespan startup, so the daemon now HOLDS the listener
    # during the ~29s lifespan startup. The socket is bound but NOT
    # listening — asyncio's ``loop.create_server(..., sock=sock)``
    # performs the ``listen()`` (uvicorn/server.py:144). A probe during
    # that window therefore still sees ECONNREFUSED, exactly as it does
    # today when the port is not bound at all (verified: SO_ACCEPTCONN
    # == 0 on the bound socket; a connect raised ConnectionRefusedError
    # errno 111, identical to the unbound case). Nothing in the boot
    # path probes 9797/9798 expecting a refusal — the boot
    # stale-subprocess sweep has no socket logic at all, and the
    # launcher's serving probe is an HTTP-level promote-ceremony probe,
    # not a boot-path "is the port free" check.
    #
    # The alternative shape (bind AFTER the lifespan startup, preserving
    # today's ordering exactly) was weighed and REJECTED: it would
    # require re-implementing uvicorn's ``Server._serve`` ordering
    # (lifespan.startup → create_server → main_loop → shutdown) or
    # patching uvicorn internals — deep coupling to a third-party
    # private API — for ZERO observable gain (both shapes answer a
    # startup-time probe with ECONNREFUSED), while LOSING the material
    # win below.
    #
    # BLAST RADIUS (independent of the wait's outcome): today every
    # failed cycle pays a FULL lifespan startup (migrations, MCP
    # warm-up, pool setup — all against the live DB; measured spawn→
    # fatal median 28.67s / min 26.13s / max 32.78s over 63 cycles)
    # followed by a FULL ~11-step teardown (median 64.31s) — and then
    # throws all of it away because the port was already gone. With the
    # pre-bind BEFORE the startup, a daemon that cannot get the port
    # performs ZERO lifespan startup steps and ZERO teardown steps.
    uvicorn_config = uvicorn.Config(
        "daemon.api:app",
        host=config.daemon.host,
        port=config.daemon.port,
        reload=False,
        access_log=False,
        timeout_graceful_shutdown=config.daemon.graceful_shutdown_timeout_seconds,
    )
    _serve_with_bind_retry(
        uvicorn_config,
        max_wait_s=config.daemon.bind_retry_max_wait_seconds,
    )


if __name__ == "__main__":
    main()
