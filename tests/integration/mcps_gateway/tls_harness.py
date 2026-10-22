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
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# ── the fence database (never ``ensemble_prod``) ──────────────────────────

FENCE_DB_NAME = "ensemble_dev_test_only"
PG_HOST = "127.0.0.1"
PG_PORT = 5432

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


@dataclass
class Frame:
    """One frame the centre sent or received."""

    direction: str  # "send" (centre -> machine) | "recv" (machine -> centre)
    raw: str
    at: float
    parsed: dict[str, Any] | None = None

    def __str__(self) -> str:  # pragma: no cover - transcript rendering
        return f"{self.direction:4} {self.raw}"


class Transcript:
    """Every frame that crossed the centre's WebSocket, in order.

    The tap is installed by this harness (in-process monkeypatch); the
    daemon source is untouched.
    """

    def __init__(self) -> None:
        self._frames: list[Frame] = []
        self._lock = asyncio.Lock()

    def add(self, direction: str, raw: str) -> Frame:
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                parsed = None
        except Exception:  # noqa: BLE001 — a non-JSON frame is recorded raw
            parsed = None
        frame = Frame(direction=direction, raw=raw, at=time.monotonic(), parsed=parsed)
        self._frames.append(frame)
        return frame

    @property
    def frames(self) -> list[Frame]:
        return list(self._frames)

    def sent(self) -> list[Frame]:
        return [f for f in self._frames if f.direction == "send"]

    def received(self) -> list[Frame]:
        return [f for f in self._frames if f.direction == "recv"]

    def find(
        self,
        *,
        direction: str | None = None,
        method: str | None = None,
        type: str | None = None,
    ) -> list[Frame]:
        out = []
        for frame in self._frames:
            if direction is not None and frame.direction != direction:
                continue
            parsed = frame.parsed or {}
            if method is not None and parsed.get("method") != method:
                continue
            if type is not None and parsed.get("type") != type:
                continue
            out.append(frame)
        return out

    def render(self, limit: int = 40) -> str:
        return "\n".join(str(f) for f in self._frames[-limit:])

    async def wait_for(
        self,
        predicate: Callable[[Frame], bool],
        *,
        timeout: float = 20.0,
        poll: float = 0.02,
    ) -> Frame | None:
        """Wait for a frame that satisfies ``predicate`` (or ``None``)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for frame in self._frames:
                if predicate(frame):
                    return frame
            await asyncio.sleep(poll)
        for frame in self._frames:
            if predicate(frame):
                return frame
        return None


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
    shutdown_seconds: float = 0.0
    ws_route_paths: list[str] = field(default_factory=list)

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
                    "user": postgres_user,
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
        "PLANE_SYNC_ENABLED": "0",
        "PLANE_MCP_ENABLED": "0",
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
        for server in ("CONTEXT7", "COMPUTER_USE", "WEBFETCH", "PLANE"):
            env[f"MCP_DISABLE_BUILT_IN_{server}"] = "true"
    env.update(postgres_env(postgres_user))
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
        task = asyncio.create_task(server.serve())
        booted: TlsGateway | None = None
        try:
            deadline = time.monotonic() + 60.0
            while not server.started:
                if time.monotonic() > deadline:
                    server.should_exit = True
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(task, timeout=SHUTDOWN_BUDGET_SECONDS)
                    raise RuntimeError(
                        "the TLS gateway server failed to start within 60s "
                        "(the lifespan boot of the real app failed)"
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
            )
            yield booted
        finally:
            # ── teardown ──
            WebSocketGatewayTransport.send_json = original_send_json  # type: ignore[method-assign]
            McpGatewayService.handle_frame = original_handle_frame  # type: ignore[method-assign]

            server.should_exit = True
            started = time.monotonic()
            completed = True
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(task, timeout=SHUTDOWN_BUDGET_SECONDS)
            if not task.done():
                completed = False
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
            if booted is not None:
                booted.server_task_completed = completed
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


__all__ = [
    "EnvPatch",
    "FENCE_DB_NAME",
    "Frame",
    "PG_HOST",
    "PG_PORT",
    "REPO_ROOT",
    "SHUTDOWN_BUDGET_SECONDS",
    "Transcript",
    "TlsGateway",
    "boot_tls_gateway",
    "cert_has_ip_san",
    "fetch_rows",
    "fence_dsn",
    "fence_sync_dsn",
    "free_tcp_port",
    "generate_self_signed_cert",
    "hex_token",
    "https_probe",
    "listeners_owned_by",
    "postgres_env",
    "reset_audit_table",
    "rows_in",
]
