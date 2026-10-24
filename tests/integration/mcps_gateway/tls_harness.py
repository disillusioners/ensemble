"""TLS mounting jig for the Phase-D1 REAL interop gate.

INDEPENDENCE / SCOPE NOTE
-------------------------
This is the ONE module of the interop lane that imports ``daemon.*``.  It is
the *mounting jig*: it boots the REAL application, holds the TLS material,
taps the centre-side frames and tears everything down.  It holds NO
protocol knowledge — the wire assertions live in ``p0_gate.py`` and the peer
is the shipped Go client (``mcps_client``), not a transcription.

What is imported from the daemon, and why:

* ``daemon.api`` — the real application composition.  The gateway route is
  registered inside ``create_app`` (conditional on ``MCPS_GATEWAY_ENABLED``)
  and the gateway SERVICE is built inside the *lifespan* (the manager owns
  the DB-backed audit sink), so the real ledger needs ``lifespan="on"``.
* ``daemon.routers.mcps_gateway.WebSocketGatewayTransport`` — tapped (NOT
  patched in the daemon) so the centre-side frame transcript can be
  observed.  The shipped client has no per-frame logging, so this tap is
  the only centre-side observability of the wire.
* ``daemon.services.mcps_gateway.service.McpGatewayService`` — tapped (NOT
  patched in the daemon) so the INBOUND frames (the ``hello``, the
  ``heartbeat``) are observable too.

WHAT IS DELIBERATELY NOT DONE HERE
----------------------------------
* ``daemon/`` source is NOT modified.  The taps are in-process
  monkeypatches installed by this harness and restored on teardown.
* The PostgreSQL target is the FENCE database ``ensemble_dev_test_only``.
  ``ensemble_prod`` is never referenced by any DSN this harness writes.
* Every listener owned by the test process is checked on teardown
  (``lsof -p <pid> -iTCP -sTCP:LISTEN``) — not only the port that was
  opened.

THE TLS FACT
------------
The shipped Go client refuses ``ws://`` at config load
(``internal/config/config.go``) and at dial (``internal/remote/transport.go``):
the remote hop is TLS only, with no escape hatch.  The daemon ships NO TLS
knob, so the app is served through uvicorn's own TLS
(``ssl_certfile``/``ssl_keyfile``) on an ephemeral port, with a per-run
self-signed certificate whose SAN is ``IP:127.0.0.1``.  The client trusts it
through ``remote.ca_pem`` (additive trust; chain verification still runs).

File-size rationale (~1050 lines):
    One mounting jig, one file: everything here is the SAME concern —
    boot the real app under TLS, tap the two seams, tear it down — and
    the interop lane's import discipline makes this the ONE sanctioned
    ``daemon.*`` importer; splitting it would multiply sanctioned
    importers, the exact thing the boundary reading forbids.
"""

from __future__ import annotations

import asyncio
import contextlib
import getpass
import json
import logging
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── the fence database (never ``ensemble_prod``) ──────────────────────────

FENCE_DB_NAME = "ensemble_dev_test_only"
PG_HOST = "127.0.0.1"
PG_PORT = 5432

#: The environment variable that EXPLICITLY names the role that owns the
#: fence database.  It is the ONLY explicit override — the ambient
#: ``POSTGRES_USER`` is NOT one (see :func:`resolve_fence_role`).
FENCE_ROLE_ENV = "MCPS_GATEWAY_FENCE_PG_USER"

#: The fence tables the daemon's boot ALTERs (``daemon/manager.py`` —
#: ``_ensure_postgres_columns``).  A role that does not OWN them cannot
#: run those statements, so they are the probe set of the preflight.
FENCE_SCHEMA_TABLES = (
    "task",
    "job_queue_items",
    "job_queues",
    "mcp_gateway_audit",
    "secrets_audit",
)

#: The repository root — the ``config.yaml`` of the daemon is read from
#: here (``ENSEMBLE_CONFIG``), so the harness does not depend on the CWD.
REPO_ROOT = Path(__file__).resolve().parents[3]

#: The gate shuts the server down with this budget.  The lifespan teardown
#: stops the worker pool, the sweeps and the monitor tasks, so 15 s (the
#: budget of the fake-client harness) is too tight for the REAL app.
SHUTDOWN_BUDGET_SECONDS = 30.0


# ── the TLS material ──────────────────────────────────────────────────────


def generate_self_signed_cert(
    tmp_dir: Path, *, common_name: str = "127.0.0.1"
) -> tuple[Path, Path]:
    """Generate a per-run self-signed certificate with an IP SAN.

    The SAN is ``IP:127.0.0.1`` — the shape the client's own fake centre
    uses.  A DNS SAN would NOT match a connection to ``127.0.0.1``.
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    cert_path = tmp_dir / "center-cert.pem"
    key_path = tmp_dir / "center-key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            str(key_path),
            "-out",
            str(cert_path),
            "-subj",
            f"/CN={common_name}",
            "-addext",
            f"subjectAltName=IP:{common_name}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return cert_path, key_path


def cert_has_ip_san(cert_path: Path, ip: str = "127.0.0.1") -> bool:
    """Does the certificate carry ``IP:<ip>`` in its SAN extension?"""
    proc = subprocess.run(
        ["openssl", "x509", "-in", str(cert_path), "-noout", "-text"],
        check=True,
        capture_output=True,
        text=True,
    )
    return f"IP Address:{ip}" in proc.stdout


# ── the centre-side frame transcript ──────────────────────────────────────
#
# The frame model lives in ``transcript.py`` — the daemon-FREE module of
# the lane (the frame log + the envelope violation recorder).  The harness
# re-exports it so there is exactly ONE frame model in the interop lane:
# the frames the harness records and the frames the assertions read are
# the same objects.

try:  # pragma: no cover - the package import is the normal one
    from .transcript import Frame, Transcript
except ImportError:  # pragma: no cover - direct script invocation
    from tests.integration.mcps_gateway.transcript import Frame, Transcript


# ── environment plumbing ──────────────────────────────────────────────────


class EnvPatch:
    """Set environment variables for the duration of a block.

    ``None`` values REMOVE the variable (and the previous value is put
    back on exit) — that is how ``DESTRUCTIVE`` is kept unset.
    """

    def __init__(self, env: dict[str, str | None]) -> None:
        self.env = env
        self._saved: dict[str, str | None] = {}

    def __enter__(self) -> "EnvPatch":
        for key, value in self.env.items():
            self._saved[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return self

    def __exit__(self, *exc: object) -> None:
        for key, old in self._saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def fence_dsn(user: str = "") -> str:
    """The DSN of the fence database (never ``ensemble_prod``).

    The plain ``postgresql://`` form is what the ASYNC side of the daemon
    wants (``daemon/persistence.py`` — psycopg3/asyncpg resolve it
    themselves).
    """
    credentials = f"{user}@" if user else ""
    return f"postgresql://{credentials}{PG_HOST}:{PG_PORT}/{FENCE_DB_NAME}"


def fence_sync_dsn(user: str = "") -> str:
    """The SYNCHRONOUS DSN of the fence database (psycopg3 driver).

    ``postgresql://`` without a driver defaults to psycopg2, which is not
    installed — the sync engine of the daemon uses psycopg3 explicitly
    (``daemon/repositories/factory.py``).
    """
    credentials = f"{user}@" if user else ""
    return f"postgresql+psycopg://{credentials}{PG_HOST}:{PG_PORT}/{FENCE_DB_NAME}"


def postgres_env(user: str = "") -> dict[str, str | None]:
    """Both sides of the split brain, pinned to the SAME fence database.

    The checkpointer honours ``POSTGRES_URL`` (``daemon/persistence.py``)
    and the repository engine honours the ``POSTGRES_*`` parts
    (``daemon/repositories/factory.py``) — pinning one side only is the
    documented split brain.
    """
    return {
        "POSTGRES_URL": fence_dsn(user),
        "POSTGRES_HOST": PG_HOST,
        "POSTGRES_PORT": str(PG_PORT),
        "POSTGRES_DB": FENCE_DB_NAME,
        "POSTGRES_USER": user,
        "POSTGRES_PASSWORD": "",
    }


# ── the fence role (who may act on the fence schema) ─────────────────────


def fence_owner_role() -> str:
    """The role that owns the fence database — the shell user."""
    return getpass.getuser()


def resolve_fence_role(requested: str = "") -> str:
    """The role the fence DSN must authenticate as.

    WHY NOT THE AMBIENT ``POSTGRES_USER``
    -------------------------------------
    The repository's ``.env`` exports ``POSTGRES_USER=ensemble`` — the
    role of the PRODUCTION database — and the ambient shell inherits it.
    The fence database (``ensemble_dev_test_only``) is owned by the
    SHELL USER, so the ``ensemble`` role does not own its tables: the
    daemon's boot ``ALTER TABLE task ...`` then fails with
    ``InsufficientPrivilege`` ("must be owner of table task"), the
    lifespan dies, and the gate used to hang for 60 s on a generic
    "the TLS gateway server failed to start within 60s" error.

    THE OVERRIDE: only ``MCPS_GATEWAY_FENCE_PG_USER`` (an explicit
    request) or a caller that names a role OTHER than the ambient one
    counts.  The ambient value is never an explicit request — it is
    exactly the thing that must not be trusted here.
    """
    explicit = (os.environ.get(FENCE_ROLE_ENV, "") or "").strip()
    if explicit:
        return explicit
    requested = (requested or "").strip()
    ambient = (os.environ.get("POSTGRES_USER", "") or "").strip()
    if requested and requested != ambient:
        return requested
    return fence_owner_role()


def preflight_fence_role(user: str) -> dict[str, Any]:
    """Assert — BEFORE the boot — that ``user`` can act on the fence schema.

    The daemon's boot runs ``ALTER TABLE …`` over the fence tables; a
    role that does not own them gets ``InsufficientPrivilege`` and the
    lifespan dies.  This preflight turns that 60-second hang into a
    one-line, actionable failure (seconds, not a minute).

    Returns a report dict — ``ok`` is True only when the role is
    connected AND may ALTER every fence table that already exists.
    """
    from sqlalchemy import create_engine, text as sa_text

    report: dict[str, Any] = {
        "ok": False,
        "role": user,
        "database": FENCE_DB_NAME,
        "owners": {},
        "not_privileged": [],
        "error": "",
    }
    engine = None
    try:
        engine = create_engine(
            fence_sync_dsn(user),
            connect_args={"options": "-c timezone=UTC"},
        )
        with engine.connect() as conn:
            current = conn.execute(sa_text("SELECT current_user")).scalar()
            report["role"] = str(current)
            # The table names are module constants — no injection surface.
            names = ", ".join(f"'{name}'" for name in FENCE_SCHEMA_TABLES)
            rows = conn.execute(
                sa_text(
                    "SELECT c.relname, pg_get_userbyid(c.relowner) AS owner, "
                    "pg_has_role(:role, c.relowner, 'MEMBER') AS can_alter "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    f"WHERE n.nspname = 'public' AND c.relname IN ({names})"
                ),
                {"role": str(current)},
            ).all()
            for tablename, owner, can_alter in rows:
                report["owners"][str(tablename)] = str(owner)
                # ALTER on a table is an OWNERSHIP right in PostgreSQL
                # (there is no 'ALTER' table privilege) — so the probe is
                # membership of the owning role.
                if not can_alter:
                    report["not_privileged"].append(
                        f"{tablename} (owner={owner})"
                    )
    except Exception as exc:  # noqa: BLE001 — the report IS the diagnosis
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report
    finally:
        if engine is not None:
            engine.dispose()
    report["ok"] = not report["not_privileged"] and not report["error"]
    return report


def fence_role_failure_message(report: dict[str, Any]) -> str:
    """The ONE actionable line of a failed fence-role preflight."""
    role = report.get("role") or "?"
    database = report.get("database", FENCE_DB_NAME)
    if report.get("error"):
        return (
            f"fence-role preflight FAILED: role {role!r} cannot reach the fence "
            f"database {database!r} ({report['error']}) — create it as the fence "
            f"owner: createdb -h {PG_HOST} -p {PG_PORT} -U "
            f"{fence_owner_role()} {database}"
        )
    blocked = ", ".join(report.get("not_privileged") or [])
    return (
        f"fence-role preflight FAILED: role {role!r} cannot ALTER the fence "
        f"tables ({blocked}) — run the gate as the fence owner "
        f"({fence_owner_role()}), or grant ALTER on them to {role!r}"
    )


# ── the startup capture (the REAL cause of a failed boot) ─────────────────


class StartupCapture(logging.Handler):
    """Capture the startup output (uvicorn + daemon) of the boot.

    The lifespan failure of the real app is logged by uvicorn's
    ``uvicorn.error`` logger WITH the exception attached; without this
    capture the gate could only report "the server failed to start",
    which hides the real cause (e.g. ``InsufficientPrivilege``).
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            self.lines.append(self.format(record))
        except Exception:  # noqa: BLE001 — a formatting failure is not fatal
            self.lines.append(record.getMessage())

    def tail(self, limit: int = 40) -> str:
        """The last ``limit`` captured lines (the startup stderr)."""
        return "\n".join(self.lines[-limit:])


def attach_startup_capture(capture: logging.Handler) -> None:
    """Attach the capture to the loggers the boot writes to.

    uvicorn's ``dictConfig`` (applied INSIDE ``serve()``) REPLACES the
    handlers of the loggers it configures, so the capture is re-attached
    by the boot loop — see :func:`boot_tls_gateway`.
    """
    for logger in (logging.getLogger(), logging.getLogger("uvicorn.error")):
        if capture not in logger.handlers:
            logger.addHandler(capture)


def startup_failure_message(
    capture: StartupCapture, exc: BaseException | None
) -> str:
    """The startup failure, with the REAL cause visible."""
    parts = [
        "the TLS gateway server failed to start within 60s "
        "(the lifespan boot of the real app failed)"
    ]
    if exc is not None:
        parts.append(f"lifespan exception: {type(exc).__name__}: {exc}")
    tail = capture.tail(40)
    if tail:
        parts.append("--- captured startup output (last 40 lines) ---\n" + tail)
    else:
        parts.append(
            "--- captured startup output: EMPTY (the daemon logged nothing "
            "at WARNING level or above) ---"
        )
    return "\n".join(parts)


# ── the booted gateway ────────────────────────────────────────────────────


@dataclass
class TlsGateway:
    """Everything the gate needs about the freshly booted centre."""

    app: Any
    service: Any
    manager: Any
    engine: Any
    config: Any
    transcript: Transcript
    host: str
    port: int
    ws_path: str
    cert_path: Path
    key_path: Path
    tokens: tuple[str, ...]
    machine_id: str
    data_dir: Path
    server: Any = None
    server_task: Any = None
    server_task_completed: bool = False
    server_task_error: str = ""
    shutdown_seconds: float = 0.0
    ws_route_paths: list[str] = field(default_factory=list)
    #: The TCP listeners of the test process captured BEFORE the server
    #: bound its socket.  A baseline taken AFTER the bind contains the
    #: gateway's OWN listener and would mask a leaked gateway listener
    #: as "pre-existing".
    listener_baseline: list[str] = field(default_factory=list)

    @property
    def ws_url(self) -> str:
        """The ``wss://`` URL of the gateway route (TLS only — the client refuses ``ws://``)."""
        return f"wss://{self.host}:{self.port}{self.ws_path}"

    @property
    def http_base(self) -> str:
        return f"https://{self.host}:{self.port}"


def _all_route_paths(app: Any) -> list[str]:
    out = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if isinstance(path, str):
            out.append(path)
    return out


def listeners_owned_by(pid: int) -> list[str]:
    """Every TCP LISTEN socket owned by ``pid`` (``lsof``).

    The teardown proof scans ALL the listeners of the test process, not
    only the port that was opened — a leaked listener on another port is
    exactly the failure this check exists for.
    """
    proc = subprocess.run(
        ["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        return []
    return lines[1:]  # drop the lsof header


@contextlib.asynccontextmanager
async def boot_tls_gateway(
    tmp_dir: Path,
    *,
    tokens: tuple[str, ...],
    machine_id: str = "mach_interop",
    heartbeat_interval_ms: int = 5000,
    heartbeat_timeout_ms: int = 15000,
    client_dead_detect_ms: int = 15000,
    command_timeout_ms: int = 30000,
    ws_path: str = "/api/mcps-gateway/ws",
    postgres_user: str = "",
    extra_env: dict[str, str | None] | None = None,
    disable_builtin_mcp: bool = True,
    tls: bool = True,
):
    """Boot the REAL application with TLS on an ephemeral port.

    The lifespan runs (``lifespan="on"``): the gateway service is the
    manager-built one, its audit sink is the DB-backed repository, and the
    ``mcp_gateway_audit`` table exists.

    Yields a :class:`TlsGateway`.  On exit the server is shut down with a
    >= 30 s budget, the listening socket is closed and the listener set of
    the process is proved empty.
    """
    import uvicorn

    tmp_dir = Path(tmp_dir)
    data_dir = tmp_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # ── the fence role (F1) ──
    # The ambient ``POSTGRES_USER`` is the role of the PRODUCTION
    # database (``ensemble``) and does NOT own the fence schema, so the
    # daemon's boot ``ALTER TABLE task …`` would raise
    # ``InsufficientPrivilege``.  The fence role is therefore pinned to
    # the fence OWNER unless the caller explicitly overrides it.
    role = resolve_fence_role(postgres_user)

    # ── the preflight (F1) — fail FAST, in seconds, not in 60 ──
    preflight = await asyncio.to_thread(preflight_fence_role, role)
    if not preflight.get("ok"):
        raise RuntimeError(fence_role_failure_message(preflight))

    # FORCE the postgres backend: the auto-detection reads the INHERITED
    # shell environment, which is exactly the thing that must not be
    # trusted here.
    (data_dir / "ensemble.json").write_text(
        json.dumps(
            {
                "database": "postgres",
                "postgres": {
                    "host": PG_HOST,
                    "port": PG_PORT,
                    "db": FENCE_DB_NAME,
                    "user": role,
                    "password": "",
                },
            }
        ),
        encoding="utf-8",
    )

    cert_path, key_path = generate_self_signed_cert(tmp_dir)

    env: dict[str, str | None] = {
        # ── the gateway knobs (read at BOOT time) ──
        "MCPS_GATEWAY_ENABLED": "1",
        "MCPS_GATEWAY_TOKENS": ",".join(tokens),
        "MCPS_GATEWAY_PATH": ws_path,
        "MCPS_GATEWAY_HEARTBEAT_INTERVAL_MS": str(heartbeat_interval_ms),
        "MCPS_GATEWAY_HEARTBEAT_TIMEOUT_MS": str(heartbeat_timeout_ms),
        "MCPS_GATEWAY_CLIENT_DEAD_DETECT_MS": str(client_dead_detect_ms),
        "MCPS_GATEWAY_COMMAND_TIMEOUT_MS": str(command_timeout_ms),
        "MCPS_GATEWAY_AUDIT_ENABLED": "1",
        "MCPS_GATEWAY_ALLOWED_MACHINES": machine_id,
        # ── the database (fence only) ──
        "ENSEMBLE_DATA_DIR": str(data_dir),
        "ENSEMBLE_CONFIG": str(REPO_ROOT / "config.yaml"),
        # ── R-23: the kill switches of the side effects ──
        # The ONLY two switches with a daemon-wide consumer are the two
        # Plane ones; the gate ASSERTS their effect on the booted app
        # (see the ``plane-*-kill-switch-effective`` checks of the gate).
        "PLANE_SYNC_ENABLED": "0",
        "PLANE_MCP_ENABLED": "0",
        # NO-OP PENDING A DAEMON KNOB: neither variable has a
        # daemon-wide consumer today (verified by grep — only
        # PLANE_SYNC_ENABLED / PLANE_MCP_ENABLED are honoured), so these
        # two lines are DOCUMENTATION, not a mitigation.  They are kept
        # (harmless) so the intent survives the day the knobs exist.
        "DRY_RUN": "1",
        "DESTRUCTIVE": None,  # UNSET
    }
    if disable_builtin_mcp:
        # The built-in MCP servers are spawned (as stdio subprocesses) by
        # the boot-time warm-up pool, and that warm-up BLOCKS the event
        # loop for ~60 s — a WSS upgrade issued inside that window times
        # out (measured: the shipped Go client needed three upgrade
        # attempts, 20 s each, before the handshake succeeded).  The
        # gateway lane does not need the MCP servers, so they are
        # switched off and the window disappears.
        for server in ("CONTEXT7", "WEBFETCH", "PLANE"):
            env[f"MCP_DISABLE_BUILT_IN_{server}"] = "true"
    env.update(postgres_env(role))
    if extra_env:
        env.update(extra_env)

    patch = EnvPatch(env)
    patch.__enter__()
    try:
        if "daemon.api" in sys.modules:
            raise RuntimeError(
                "daemon.api is already imported: the gateway knobs and the "
                "database are read at IMPORT time, so this lane needs a "
                "process where daemon.api has not been imported yet "
                "(run it in isolation)."
            )
        import daemon.api as daemon_api

        app = daemon_api.app
        route_paths = _all_route_paths(app)
        if ws_path not in route_paths:
            raise RuntimeError(
                f"the gateway route {ws_path} is not registered: "
                f"MCPS_GATEWAY_ENABLED was not honoured at boot "
                f"(routes: {route_paths[:20]})"
            )

        # ── the lifespan tap (the REAL exception of a failed boot) ──
        # The daemon's lifespan raises when the boot fails (e.g. the
        # ``InsufficientPrivilege`` of an ALTER on a table the role does
        # not own).  uvicorn swallows it into a log line, so the harness
        # taps the lifespan context and keeps the exception itself.
        startup_failure: dict[str, BaseException | None] = {"exc": None}
        original_lifespan = app.router.lifespan_context

        @contextlib.asynccontextmanager
        async def tapped_lifespan(inner_app: Any):
            try:
                async with original_lifespan(inner_app) as state:
                    yield state
            except BaseException as exc:  # noqa: BLE001 — the failure IS the evidence
                startup_failure["exc"] = exc
                raise

        app.router.lifespan_context = tapped_lifespan

        # ── the taps (in-process; the daemon source is NOT modified) ──
        from daemon.routers.mcps_gateway import WebSocketGatewayTransport
        from daemon.services.mcps_gateway.service import McpGatewayService

        transcript = Transcript()
        original_send_json = WebSocketGatewayTransport.send_json
        original_handle_frame = McpGatewayService.handle_frame

        async def tapped_send_json(self, payload):  # type: ignore[no-untyped-def]
            transcript.add("send", json.dumps(payload))
            return await original_send_json(self, payload)

        async def tapped_handle_frame(self, connection, raw):  # type: ignore[no-untyped-def]
            transcript.add("recv", raw)
            return await original_handle_frame(self, connection, raw)

        WebSocketGatewayTransport.send_json = tapped_send_json  # type: ignore[method-assign]
        McpGatewayService.handle_frame = tapped_handle_frame  # type: ignore[method-assign]

        config_obj = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            ssl_certfile=str(cert_path) if tls else None,
            ssl_keyfile=str(key_path) if tls else None,
            lifespan="on",
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config_obj)

        # ── the listener baseline (F3) ──
        # It MUST be captured BEFORE the server binds its socket: a
        # baseline taken afterwards contains the gateway's OWN listener
        # and would mask a leaked gateway listener as "pre-existing".
        listener_baseline = await asyncio.to_thread(listeners_owned_by, os.getpid())

        # ── the startup capture (F1) ──
        startup_capture = StartupCapture()
        attach_startup_capture(startup_capture)

        task = asyncio.create_task(server.serve())
        booted: TlsGateway | None = None
        try:
            deadline = time.monotonic() + 60.0
            while not server.started:
                # uvicorn's ``dictConfig`` runs INSIDE ``serve()`` and
                # REPLACES the handlers of the loggers it configures —
                # re-attach the capture so the lifespan error is kept.
                attach_startup_capture(startup_capture)
                if time.monotonic() > deadline:
                    server.should_exit = True
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(task, timeout=SHUTDOWN_BUDGET_SECONDS)
                    raise RuntimeError(
                        startup_failure_message(startup_capture, startup_failure["exc"])
                    )
                await asyncio.sleep(0.02)

            assert server.servers, "uvicorn reported started but exposed no server"
            sock = server.servers[0].sockets[0]
            port = sock.getsockname()[1]

            manager = getattr(app.state, "manager", None)
            if manager is None:
                raise RuntimeError(
                    "app.state.manager is missing: the lifespan did not run"
                )
            service = getattr(app.state, "mcps_gateway_service", None)
            if service is None:
                raise RuntimeError(
                    "app.state.mcps_gateway_service is missing: the lifespan did not run"
                )

            booted = TlsGateway(
                app=app,
                service=service,
                manager=manager,
                engine=manager.engine,
                config=getattr(service, "config", None),
                transcript=transcript,
                host="127.0.0.1",
                port=port,
                ws_path=ws_path,
                cert_path=cert_path,
                key_path=key_path,
                tokens=tokens,
                machine_id=machine_id,
                data_dir=data_dir,
                server=server,
                server_task=task,
                ws_route_paths=route_paths,
                listener_baseline=list(listener_baseline),
            )
            yield booted
        finally:
            # ── teardown ──
            WebSocketGatewayTransport.send_json = original_send_json  # type: ignore[method-assign]
            McpGatewayService.handle_frame = original_handle_frame  # type: ignore[method-assign]
            app.router.lifespan_context = original_lifespan
            for logger in (logging.getLogger(), logging.getLogger("uvicorn.error")):
                if startup_capture in logger.handlers:
                    logger.removeHandler(startup_capture)

            server.should_exit = True
            started = time.monotonic()
            completed = False
            task_error = ""
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(task, timeout=SHUTDOWN_BUDGET_SECONDS)
            if not task.done():
                # The task did not finish inside the budget — cancel it.
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
            if task.done():
                # A CRASHED task is ALSO ``done()`` — the completion test
                # must look at the exception, not at ``done()`` alone.
                if task.cancelled():
                    completed = False
                    task_error = "the server task was cancelled"
                else:
                    try:
                        task_exc = task.exception()
                    except asyncio.CancelledError:  # pragma: no cover - defensive
                        task_exc = None
                    completed = task_exc is None
                    if task_exc is not None:
                        task_error = f"{type(task_exc).__name__}: {task_exc}"
            else:
                task_error = (
                    "the server task did not finish within the "
                    f"{SHUTDOWN_BUDGET_SECONDS:.0f}s shutdown budget"
                )
            if booted is not None:
                booted.server_task_completed = completed
                booted.server_task_error = task_error
                booted.shutdown_seconds = time.monotonic() - started
            with contextlib.suppress(Exception):
                sock.close()
            await asyncio.sleep(0.1)
    finally:
        patch.__exit__(None, None, None)


async def https_probe_async(
    port: int, path: str = "/api/health", timeout: float = 5.0
) -> tuple[int | None, float, str]:
    """The responsiveness probe — WITHOUT blocking the event loop.

    The probe is a blocking socket call, so it runs in a worker thread: a
    blocking probe INSIDE the event loop would stall the very server it
    measures (the daemon could not answer while the loop was parked in
    ``recv``).  That mistake is the reason the first two attempts of this
    gate failed: the probe looked like a daemon defect.
    """
    return await asyncio.to_thread(https_probe, port, path, timeout)


def free_tcp_port(host: str = "127.0.0.1") -> int:
    """An ephemeral port that is free RIGHT NOW (bind, read, release)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def https_probe(
    port: int, path: str = "/api/health", timeout: float = 5.0
) -> tuple[int | None, float, str]:
    """A TLS HTTP probe against the booted app.

    The certificate is self-signed, so the verification is switched off —
    this is a LOCAL responsiveness probe, not a trust decision.  It
    measures whether the daemon's EVENT LOOP answers: a loop that is
    blocked by a boot-time task answers NOTHING (the probe times out).
    """
    import http.client
    import ssl

    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    started = time.monotonic()
    connection = http.client.HTTPSConnection(
        "127.0.0.1", port, context=context, timeout=timeout
    )
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read(200).decode("utf-8", "replace")
        return response.status, time.monotonic() - started, body
    except Exception as exc:  # noqa: BLE001 — a timeout IS the measurement
        return None, time.monotonic() - started, f"{type(exc).__name__}: {exc}"
    finally:
        with contextlib.suppress(Exception):
            connection.close()


def reset_audit_table(engine: Any) -> int:
    """Empty the fence ledger (the FENCE database only).

    The ledger is the evidence of the gate, so every attempt starts from
    an empty one: a row of a PREVIOUS attempt must never be mistaken for
    the row of THIS one.  The fence database is asserted first — this
    helper must never be pointed at ``ensemble_prod``.
    """
    from sqlalchemy import text as sa_text

    url = str(engine.url)
    if FENCE_DB_NAME not in url:
        raise RuntimeError(
            f"refusing to empty the audit table of {url!r}: the fence "
            f"database {FENCE_DB_NAME} is not the target"
        )
    with engine.begin() as conn:
        deleted = conn.execute(sa_text("DELETE FROM mcp_gateway_audit")).rowcount
    return int(deleted or 0)


def gateway_error_code(exc: BaseException) -> str:
    """The typed code of a gateway refusal — or ``""`` for anything else.

    The daemon's own exception carries the §6.5 code (``MACHINE_BUSY``,
    ``TOOL_TIMEOUT``, …).  The interop lane must not import
    ``daemon.*`` itself, so the ONE sanctioned importer reads the code
    here and hands the plain string over.
    """
    from daemon.services.mcps_gateway.protocol import GatewayProtocolError

    if isinstance(exc, GatewayProtocolError):
        return str(exc.code)
    return ""


def gateway_error_data(exc: BaseException) -> dict[str, Any]:
    """The ``data`` member of a gateway refusal (an empty dict if none)."""
    from daemon.services.mcps_gateway.protocol import GatewayProtocolError

    if isinstance(exc, GatewayProtocolError):
        return dict(exc.data or {})
    return {}


def issue_read_bearer(
    engine: Any,
    *,
    label: str = "interop-read",
    scopes: tuple[str, ...] = ("mcp:read",),
    ttl: int = 3600,
) -> str:
    """Mint a bearer token through the daemon's OWN mint core.

    The read-parity lane runs with AUTH ON (B12): the decorator that
    guards the read surface is INERT when auth is off, so an auth-off
    lane would vacate exactly the behaviour the read surface has to
    prove.  This helper calls the same ``_issue_token`` the operator CLI
    calls — the token is a real row in ``auth_bearer_tokens``, hashed
    exactly like an operator's, and the plaintext is returned ONCE.

    WHY THE DAEMON IMPORT LIVES HERE: the interop lane has exactly ONE
    module that may import ``daemon.*`` (this one).  The assertion file
    stays daemon-free and only ever sees the plaintext token.
    """
    from daemon.auth.cli import _issue_token

    return _issue_token(
        label=label,
        ttl=ttl,
        scopes=tuple(scopes),
        bootstrap_material=None,
        engine=engine,
        bearer_ttl_default=ttl,
        pid=os.getpid(),
    )


def plane_switches_effective() -> tuple[bool, str]:
    """Are the two Plane kill switches EFFECTIVE on the booted app?

    ``DRY_RUN`` / ``DESTRUCTIVE`` have NO daemon-wide consumer today
    (verified by grep — only the two Plane switches are honoured), so
    they are NO-OPS PENDING A KNOB: they gate NOTHING and they are never
    cited as an effective mitigation.  The real protection of the lane is
    the job-queue assertion, the PID-scoped listener scan and the
    machine-scoped DB assertions.

    The switches themselves are read back through the daemon's OWN
    readers, so the EFFECT is measured on the booted application instead
    of being assumed from the environment.
    """
    from daemon.mcp.builtin_servers import plane as plane_builtin
    from daemon.mcp.builtin_servers.plane import PlaneServerDefinition
    from daemon.services.plane_sync_service import (
        PlaneSyncService,
        plane_sync_enabled,
    )

    sync_enabled = plane_sync_enabled()
    sync_reason = PlaneSyncService.unavailable_reason()
    switch_off = plane_builtin._mcp_kill_switch_enabled() is False
    available = PlaneServerDefinition.is_available()
    ok = (
        sync_enabled is False
        and sync_reason == "disabled by kill-switch (PLANE_SYNC_ENABLED=false)"
        and switch_off
        and available is False
    )
    return ok, (
        f"sync_enabled={sync_enabled} reason={sync_reason!r} "
        f"mcp_switch_off={switch_off} mcp_available={available}"
    )


def hex_token() -> str:
    """A 32-byte hex credential (the shape the Go client requires)."""
    return subprocess.run(
        ["openssl", "rand", "-hex", "32"], check=True, capture_output=True, text=True
    ).stdout.strip()


def rows_in(
    engine: Any, table: str, where: str = "", params: dict[str, Any] | None = None
) -> int:
    """Count the rows of ``table`` (optionally filtered) on ``engine``."""
    from sqlalchemy import text as sa_text

    sql = f"SELECT count(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    with engine.connect() as conn:
        return int(conn.execute(sa_text(sql), params or {}).scalar() or 0)


def fetch_rows(engine: Any, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Run a read-only query on ``engine`` and return the rows as dicts."""
    from sqlalchemy import text as sa_text

    with engine.connect() as conn:
        result = conn.execute(sa_text(sql), params or {})
        return [dict(row) for row in result.mappings()]


def independent_fence_engine(user: str = "") -> Any:
    """A SECOND engine on the SAME fence DSN, built independently.

    The P3 audit proof reads the ledger back through an engine the test
    constructed itself — never through the manager's pool — so the
    round-trip proof cannot share a connection cache (or a transaction)
    with the writer.  The connect args are the daemon's own PG session
    pins, reached through this module (the sanctioned importer).
    """
    from sqlalchemy import create_engine

    from daemon.repositories.factory import PG_SESSION_CONNECT_ARGS

    return create_engine(
        fence_sync_dsn(resolve_fence_role(user)),
        connect_args=dict(PG_SESSION_CONNECT_ARGS),
    )


def record_audit_canary(
    service: Any,
    *,
    agent_id: str,
    machine_id: str,
    tool: str,
    args_hash: str,
    outcome: str = "ok",
    session_id: str = "",
    detail: str = "",
) -> None:
    """Write ONE canary audit row through the service's OWN sink.

    The record type is a daemon type; the interop lane reaches it
    through this module (the sanctioned importer) so the assertion
    file stays daemon-import-free.  Synchronous on purpose — the
    caller bridges with ``asyncio.to_thread``.
    """
    from daemon.services.mcps_gateway.audit import AuditRecord

    service._audit.record(
        AuditRecord(
            agent_id=agent_id,
            machine_id=machine_id,
            tool=tool,
            args_hash=args_hash,
            outcome=outcome,
            session_id=session_id,
            detail=detail,
        )
    )


__all__ = [
    "EnvPatch",
    "FENCE_DB_NAME",
    "FENCE_ROLE_ENV",
    "FENCE_SCHEMA_TABLES",
    "Frame",
    "PG_HOST",
    "PG_PORT",
    "REPO_ROOT",
    "SHUTDOWN_BUDGET_SECONDS",
    "StartupCapture",
    "Transcript",
    "TlsGateway",
    "attach_startup_capture",
    "boot_tls_gateway",
    "cert_has_ip_san",
    "fetch_rows",
    "fence_dsn",
    "fence_owner_role",
    "fence_role_failure_message",
    "fence_sync_dsn",
    "free_tcp_port",
    "gateway_error_code",
    "gateway_error_data",
    "generate_self_signed_cert",
    "hex_token",
    "https_probe",
    "independent_fence_engine",
    "issue_read_bearer",
    "listeners_owned_by",
    "plane_switches_effective",
    "postgres_env",
    "preflight_fence_role",
    "record_audit_canary",
    "reset_audit_table",
    "resolve_fence_role",
    "rows_in",
    "startup_failure_message",
]
