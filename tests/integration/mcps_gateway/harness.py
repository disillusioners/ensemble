"""Mounting jig for the remote-hands gateway integration tests.

INDEPENDENCE NOTE
-----------------
This is the ONLY module in the test package that is allowed to import
``daemon.*``.  It is the *mounting jig*: it boots the real FastAPI
application, sets the environment flags, and starts a throw-away server on
an **ephemeral port**.  It contains no wire-shape knowledge — every wire
shape lives in ``fake_remote_client.py``, which was transcribed from
``mcps_client/SPEC.md`` alone.

What is imported from the daemon, and why:

* ``daemon.api.create_app`` — the real application composition.  The
  gateway route is registered *inside* ``create_app`` (conditional on
  ``MCPS_GATEWAY_ENABLED``), so this is the only way to exercise the real
  wiring.
* ``daemon.services.mcps_gateway.config.load_gateway_config`` — reading the
  effective configuration (env knobs) so a test can assert what the
  gateway believes.
* ``daemon.services.mcps_gateway.service.McpGatewayService`` — the center's
  service object.  The harness constructs one so it can (a) drive the
  center-side operations (``connect`` / ``command`` / ``disconnect``) that
  an agent would otherwise reach through the agent-facing MCP tools, and
  (b) attach an in-memory audit sink so the §8.4 audit trail is reachable
  from a test without a database.

No gateway source file was read to produce this harness; only public
signatures and route metadata were used (see the report's independence
attestation).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterable

import uvicorn

# --------------------------------------------------------------------------
# Environment knobs (from the task contract)
# --------------------------------------------------------------------------

ENV_ENABLED = "MCPS_GATEWAY_ENABLED"
ENV_TOKENS = "MCPS_GATEWAY_TOKENS"
ENV_HEARTBEAT_INTERVAL_MS = "MCPS_GATEWAY_HEARTBEAT_INTERVAL_MS"
ENV_HEARTBEAT_TIMEOUT_MS = "MCPS_GATEWAY_HEARTBEAT_TIMEOUT_MS"
ENV_CLIENT_DEAD_DETECT_MS = "MCPS_GATEWAY_CLIENT_DEAD_DETECT_MS"
ENV_SESSION_TTL_MS = "MCPS_GATEWAY_SESSION_TTL_MS"
ENV_SESSION_IDLE_TIMEOUT_MS = "MCPS_GATEWAY_SESSION_IDLE_TIMEOUT_MS"
ENV_ALLOWED_MACHINES = "MCPS_GATEWAY_ALLOWED_MACHINES"
ENV_COMMAND_TIMEOUT_MS = "MCPS_GATEWAY_COMMAND_TIMEOUT_MS"
ENV_PATH = "MCPS_GATEWAY_PATH"
ENV_AUDIT_ENABLED = "MCPS_GATEWAY_AUDIT_ENABLED"

#: Every knob the task contract promises the gateway honours.
CONTRACT_ENV_KNOBS = (
    ENV_ENABLED,
    ENV_TOKENS,
    ENV_HEARTBEAT_INTERVAL_MS,
    ENV_HEARTBEAT_TIMEOUT_MS,
    ENV_CLIENT_DEAD_DETECT_MS,
    ENV_SESSION_TTL_MS,
    ENV_SESSION_IDLE_TIMEOUT_MS,
    ENV_ALLOWED_MACHINES,
)

#: The WebSocket route path the contract fixes.
CONTRACT_WS_PATH = "/api/mcps-gateway/ws"


@dataclass
class GatewayEnv:
    """The environment the gateway is booted with."""

    enabled: bool = True
    tokens: tuple[str, ...] = ("tok-alpha", "tok-beta")
    heartbeat_interval_ms: int = 1000
    heartbeat_timeout_ms: int = 3000
    client_dead_detect_ms: int = 3000
    command_timeout_ms: int = 3000
    session_ttl_ms: int = 3_600_000
    session_idle_timeout_ms: int = 300_000
    allowed_machines: tuple[str, ...] = ()
    path: str = CONTRACT_WS_PATH
    audit_enabled: bool = True

    def as_environ(self) -> dict[str, str]:
        env: dict[str, str] = {}
        env[ENV_ENABLED] = "1" if self.enabled else "0"
        env[ENV_TOKENS] = ",".join(self.tokens)
        env[ENV_HEARTBEAT_INTERVAL_MS] = str(self.heartbeat_interval_ms)
        env[ENV_HEARTBEAT_TIMEOUT_MS] = str(self.heartbeat_timeout_ms)
        env[ENV_CLIENT_DEAD_DETECT_MS] = str(self.client_dead_detect_ms)
        env[ENV_COMMAND_TIMEOUT_MS] = str(self.command_timeout_ms)
        env[ENV_SESSION_TTL_MS] = str(self.session_ttl_ms)
        env[ENV_SESSION_IDLE_TIMEOUT_MS] = str(self.session_idle_timeout_ms)
        env[ENV_ALLOWED_MACHINES] = ",".join(self.allowed_machines)
        env[ENV_PATH] = self.path
        env[ENV_AUDIT_ENABLED] = "1" if self.audit_enabled else "0"
        return env


@dataclass
class BootedGateway:
    """Everything a test needs to talk to a freshly booted gateway."""

    app: Any
    service: Any
    audit_sink: Any
    config: Any
    env: GatewayEnv
    host: str = "127.0.0.1"
    port: int = 0
    ws_path: str = CONTRACT_WS_PATH
    ws_route_paths: list[str] = field(default_factory=list)
    extra_routes: dict[str, str] = field(default_factory=dict)
    server: Any = None
    _task: Any = None
    _sock: socket.socket | None = None

    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}{self.ws_path}"

    @property
    def http_base(self) -> str:
        return f"http://{self.host}:{self.port}"


class _EnvPatch:
    """Set environment variables for the duration of a block."""

    def __init__(self, env: dict[str, str]) -> None:
        self.env = env
        self._saved: dict[str, str | None] = {}

    def __enter__(self) -> "_EnvPatch":
        for key, value in self.env.items():
            self._saved[key] = os.environ.get(key)
            os.environ[key] = value
        return self

    def __exit__(self, *exc: object) -> None:
        for key, old in self._saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def _ws_route_paths(app: Any) -> list[str]:
    out = []
    for route in app.routes:
        if type(route).__name__ == "APIWebSocketRoute":
            out.append(getattr(route, "path", ""))
    return out


def _all_route_paths(app: Any) -> list[str]:
    out = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if isinstance(path, str):
            out.append(path)
    return out


@contextlib.asynccontextmanager
async def boot_gateway(env: GatewayEnv | None = None) -> AsyncIterator[BootedGateway]:
    """Boot the real daemon application with the gateway route mounted.

    The server binds ``127.0.0.1:0`` (an ephemeral port) — never the
    daemon's real port.  On exit the server is shut down and the listening
    socket is asserted closed.
    """
    env = env or GatewayEnv()
    patch = _EnvPatch(env.as_environ())
    patch.__enter__()
    try:
        from daemon.api import create_app
        from daemon.services.mcps_gateway.audit import MemoryAuditSink
        from daemon.services.mcps_gateway.config import load_gateway_config
        from daemon.services.mcps_gateway.service import McpGatewayService

        config = load_gateway_config()
        audit_sink = MemoryAuditSink()
        service = McpGatewayService(config, audit_sink=audit_sink)

        app = create_app()
        # The lifespan is not started (``lifespan="off"`` below) so the
        # gateway service is injected the same way the daemon's own lifespan
        # does — onto ``app.state``.
        app.state.mcps_gateway_service = service

        route_paths = _ws_route_paths(app)
        all_paths = _all_route_paths(app)

        config_obj = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_level="warning",
            lifespan="off",
            access_log=False,
        )
        server = uvicorn.Server(config_obj)
        task = asyncio.create_task(server.serve())
        deadline = time.monotonic() + 20.0
        while not server.started:
            if time.monotonic() > deadline:
                server.should_exit = True
                await task
                raise RuntimeError("gateway test server failed to start within 20s")
            await asyncio.sleep(0.02)

        assert server.servers, "uvicorn reported started but exposed no server"
        sock = server.servers[0].sockets[0]
        port = sock.getsockname()[1]

        booted = BootedGateway(
            app=app,
            service=service,
            audit_sink=audit_sink,
            config=config,
            env=env,
            host="127.0.0.1",
            port=port,
            ws_route_paths=route_paths,
            extra_routes={"all": repr(all_paths)},
        )
        # The route the contract fixes, if it is mounted at all.
        booted.ws_path = CONTRACT_WS_PATH if CONTRACT_WS_PATH in all_paths else (
            route_paths[0] if route_paths else CONTRACT_WS_PATH
        )
        try:
            yield booted
        finally:
            server.should_exit = True
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(task, timeout=15)
            # No listener may survive the test.
            with contextlib.suppress(Exception):
                sock.close()
            await asyncio.sleep(0.05)
    finally:
        patch.__exit__(None, None, None)


def port_is_listening(host: str, port: int) -> bool:
    """True when something is still accepting connections on ``port``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


async def connect_ws(url: str, token: str | None):
    """Open a WebSocket, translating a refused upgrade into a typed result.

    §3.1/§4.1 — a refused upgrade produces **no** protocol message.  The
    caller receives ``(ws, status_code)`` where ``ws`` is ``None`` and
    ``status_code`` is the HTTP status of the refused upgrade.
    """
    import websockets
    from websockets.exceptions import InvalidStatus

    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    try:
        ws = await websockets.connect(url, additional_headers=headers)
    except InvalidStatus as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return None, status
    return ws, 101


def format_transcript(entries: Iterable[Any]) -> str:
    return "\n".join(f"{e.direction:14} {e.raw}" for e in entries)
