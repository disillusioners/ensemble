"""The remote-hands gateway configuration — every knob under one prefix.

The kill switch is ``MCPS_GATEWAY_ENABLED`` and it is **DEFAULT OFF**.
When it is off the WebSocket route is NOT REGISTERED AT ALL (the daemon
answers 404 on the path) — not "registered but refusing". The tool layer
refuses too, with a typed error, so the tool surface never looks healthy
while the transport is off.

The knob set (all under the ``MCPS_GATEWAY_`` prefix)::

    MCPS_GATEWAY_ENABLED                 false    the kill switch
    MCPS_GATEWAY_PATH                    /api/mcps-gateway/ws
    MCPS_GATEWAY_TOKENS                  ""       comma-separated enrollment tokens
    MCPS_GATEWAY_HEARTBEAT_INTERVAL_MS   15000
    MCPS_GATEWAY_HEARTBEAT_TIMEOUT_MS    45000
    MCPS_GATEWAY_CLIENT_DEAD_DETECT_MS   45000
    MCPS_GATEWAY_COMMAND_TIMEOUT_MS      30000
    MCPS_GATEWAY_AUDIT_ENABLED           true
    MCPS_GATEWAY_ALLOWED_MACHINES        ""       comma-separated; empty = all
    MCPS_GATEWAY_SESSION_TTL_MS          3600000
    MCPS_GATEWAY_SESSION_IDLE_TIMEOUT_MS 300000

``MCPS_GATEWAY_TOKENS`` empty means **nobody may enroll** — fail closed.
The tokens are compared with :func:`hmac.compare_digest`; there is no
plaintext downgrade and no fallback path.

The THREE heartbeat parameters of §3.3 are all knobs: the center
advertises all three in its hello result and the client adopts them
(§3.3: "The client MUST adopt the server's values; the client does not
get to pick"). ``HEARTBEAT_TIMEOUT_MS`` is the SERVER's own silence
tolerance — the window after which the center declares the machine dead
(§3.3) — and ``CLIENT_DEAD_DETECT_MS`` is the value the center
advertises for the CLIENT's death detector.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: The prefix every knob of this subsystem lives under.
ENV_PREFIX = "MCPS_GATEWAY_"

#: The default route path (§5.4 of the SPEC shows a path; the leader
#: contract froze the daemon-side one).
DEFAULT_PATH = "/api/mcps-gateway/ws"

#: The default heartbeat interval (§3.3).
DEFAULT_HEARTBEAT_INTERVAL_MS = 15_000

#: The default server-side silence tolerance (§3.3) — the window after
#: which the center declares the machine dead.
DEFAULT_HEARTBEAT_TIMEOUT_MS = 45_000

#: The default client-dead-detect window (§3.3) — the value the center
#: advertises for the CLIENT's death detector.
DEFAULT_CLIENT_DEAD_DETECT_MS = 45_000

#: The default per-command deadline of the center. The SPEC's §9.3 makes
#: the timeout the CLIENT's job; this is the CENTER's own bound on how
#: long it waits for the machine to answer, so a wedged machine cannot
#: hang an agent tool forever.
DEFAULT_COMMAND_TIMEOUT_MS = 30_000

#: The default session TTL (§3.3 — the center owns the enforcement).
DEFAULT_SESSION_TTL_MS = 3_600_000

#: The default session idle timeout (§3.3).
DEFAULT_SESSION_IDLE_TIMEOUT_MS = 300_000

#: The realm of the protection space the credential belongs to.
#:
#: SPEC.md v0.1 is SILENT on the realm (a grep for ``realm`` over the spec
#: returns zero matches), so the value is a choice of the implementation.
#: It is advisory (RFC 6750 gives it no meaning) — the shipped client
#: reads the STATUS and the BODY of a refusal, not the realm.
AUTH_REALM = "ensemble-gateway"

_TRUTHY = ("1", "true", "yes", "on")
_FALSY = ("0", "false", "no", "off")


def _env(name: str) -> str:
    """Read one knob, stripped. Missing → empty string."""
    return os.environ.get(ENV_PREFIX + name, "").strip()


def _env_bool(name: str, default: bool) -> bool:
    """Read one boolean knob.

    The convention of the repo (``daemon/services/plane_sync_service.py``):
    an unset knob takes the default; ``0`` / ``false`` / ``no`` / ``off``
    (case-insensitive) is the explicit off; any other non-empty value is
    the explicit on.
    """
    raw = _env(name).lower()
    if not raw:
        return default
    if raw in _FALSY:
        return False
    if raw in _TRUTHY:
        return True
    logger.warning(
        "%s%s=%r is not a recognised boolean; the default (%s) is used",
        ENV_PREFIX,
        name,
        raw,
        default,
    )
    return default


def _env_int(name: str, default: int, warnings: list[str] | None = None) -> int:
    """Read one integer knob. An unparseable value takes the default.

    The refusal is both LOGGED and COLLECTED in ``warnings`` — the
    operator sees it in the log and the caller (and the tests) can see
    it in the configuration object.
    """
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        message = (
            f"{ENV_PREFIX}{name}={raw!r} is not an integer; the default "
            f"({default}) is used"
        )
        logger.warning(message)
        if warnings is not None:
            warnings.append(message)
        return default


def _env_list(name: str) -> tuple[str, ...]:
    """Read one comma-separated knob into a tuple of non-empty entries."""
    raw = _env(name)
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class GatewayConfig:
    """The resolved configuration of the remote-hands gateway."""

    enabled: bool = False
    path: str = DEFAULT_PATH
    tokens: tuple[str, ...] = ()
    heartbeat_interval_ms: int = DEFAULT_HEARTBEAT_INTERVAL_MS
    heartbeat_timeout_ms: int = DEFAULT_HEARTBEAT_TIMEOUT_MS
    client_dead_detect_ms: int = DEFAULT_CLIENT_DEAD_DETECT_MS
    command_timeout_ms: int = DEFAULT_COMMAND_TIMEOUT_MS
    audit_enabled: bool = True
    allowed_machines: tuple[str, ...] = ()
    session_ttl_ms: int = DEFAULT_SESSION_TTL_MS
    session_idle_timeout_ms: int = DEFAULT_SESSION_IDLE_TIMEOUT_MS
    warnings: tuple[str, ...] = field(default=(), repr=False)

    @property
    def has_enrollment_tokens(self) -> bool:
        """Is anybody allowed to enroll? Empty tokens = nobody."""
        return bool(self.tokens)

    def machine_allowed(self, machine_id: str) -> bool:
        """Is this machine id on the roster?

        An EMPTY ``allowed_machines`` means every AUTHENTICATED machine is
        enrolled (§8.1 reconnect tolerance: a machine that reconnects
        after a blip re-appears in the roster, it does not re-enroll).
        A non-empty list is an allowlist — a machine outside it is
        ``MACHINE_NOT_ENROLLED``.
        """
        if not self.allowed_machines:
            return True
        return machine_id in self.allowed_machines

    def token_is_valid(self, candidate: str) -> bool:
        """Constant-time check of one candidate credential.

        EVERY configured token is compared — the loop does not short
        circuit on the first match, so the comparison time does not leak
        WHICH token was presented.
        """
        import hmac

        matched = False
        for token in self.tokens:
            if hmac.compare_digest(token, candidate):
                matched = True
        return matched


def load_gateway_config() -> GatewayConfig:
    """Read the configuration from the environment.

    The read happens at CALL time, never at import time — a daemon that
    boots before the operator exports the knobs must still see the final
    values, and a test can flip a knob with ``monkeypatch.setenv``.
    """
    warnings: list[str] = []

    interval = _env_int("HEARTBEAT_INTERVAL_MS", DEFAULT_HEARTBEAT_INTERVAL_MS, warnings)
    heartbeat_timeout = _env_int(
        "HEARTBEAT_TIMEOUT_MS", DEFAULT_HEARTBEAT_TIMEOUT_MS, warnings
    )
    dead_detect = _env_int("CLIENT_DEAD_DETECT_MS", DEFAULT_CLIENT_DEAD_DETECT_MS, warnings)
    command_timeout = _env_int("COMMAND_TIMEOUT_MS", DEFAULT_COMMAND_TIMEOUT_MS, warnings)
    ttl = _env_int("SESSION_TTL_MS", DEFAULT_SESSION_TTL_MS, warnings)
    idle = _env_int("SESSION_IDLE_TIMEOUT_MS", DEFAULT_SESSION_IDLE_TIMEOUT_MS, warnings)

    if interval <= 0:
        warnings.append(
            f"HEARTBEAT_INTERVAL_MS={interval} is not positive; the default "
            f"({DEFAULT_HEARTBEAT_INTERVAL_MS}) is used"
        )
        interval = DEFAULT_HEARTBEAT_INTERVAL_MS
    if heartbeat_timeout <= 0:
        warnings.append(
            f"HEARTBEAT_TIMEOUT_MS={heartbeat_timeout} is not positive; the "
            f"default ({DEFAULT_HEARTBEAT_TIMEOUT_MS}) is used"
        )
        heartbeat_timeout = DEFAULT_HEARTBEAT_TIMEOUT_MS
    if dead_detect <= 0:
        warnings.append(
            f"CLIENT_DEAD_DETECT_MS={dead_detect} is not positive; the default "
            f"({DEFAULT_CLIENT_DEAD_DETECT_MS}) is used"
        )
        dead_detect = DEFAULT_CLIENT_DEAD_DETECT_MS
    if dead_detect < 3 * interval:
        # §3.3: the timeout must be at least 3x the interval. The value is
        # REPORTED, not clamped — the operator picked it.
        warnings.append(
            f"CLIENT_DEAD_DETECT_MS={dead_detect} is below 3x the heartbeat "
            f"interval ({interval}); the §3.3 table expects at least "
            f"{3 * interval}"
        )
    if command_timeout <= 0:
        warnings.append(
            f"COMMAND_TIMEOUT_MS={command_timeout} is not positive; the "
            f"default ({DEFAULT_COMMAND_TIMEOUT_MS}) is used"
        )
        command_timeout = DEFAULT_COMMAND_TIMEOUT_MS

    path = _env("PATH") or DEFAULT_PATH
    if not path.startswith("/"):
        path = "/" + path

    return GatewayConfig(
        enabled=_env_bool("ENABLED", False),
        path=path,
        tokens=_env_list("TOKENS"),
        heartbeat_interval_ms=interval,
        heartbeat_timeout_ms=heartbeat_timeout,
        client_dead_detect_ms=dead_detect,
        command_timeout_ms=command_timeout,
        audit_enabled=_env_bool("AUDIT_ENABLED", True),
        allowed_machines=_env_list("ALLOWED_MACHINES"),
        session_ttl_ms=ttl,
        session_idle_timeout_ms=idle,
        warnings=tuple(warnings),
    )


def gateway_enabled() -> bool:
    """Is the gateway transport ON? (Default OFF.)"""
    return load_gateway_config().enabled


__all__ = [
    "AUTH_REALM",
    "DEFAULT_CLIENT_DEAD_DETECT_MS",
    "DEFAULT_COMMAND_TIMEOUT_MS",
    "DEFAULT_HEARTBEAT_INTERVAL_MS",
    "DEFAULT_HEARTBEAT_TIMEOUT_MS",
    "DEFAULT_PATH",
    "DEFAULT_SESSION_IDLE_TIMEOUT_MS",
    "DEFAULT_SESSION_TTL_MS",
    "ENV_PREFIX",
    "GatewayConfig",
    "gateway_enabled",
    "load_gateway_config",
]
