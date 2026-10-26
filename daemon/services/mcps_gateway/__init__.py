"""The remote-hands gateway — the CENTER side of SPEC.md v0.1.

``mcps_client`` is the wrapper that runs on a controlled machine; it
dials OUT to this center over WebSocket and the center brokers the
sessions and forwards the MCP traffic (SPEC.md §2, §7).

The package is importable WITHOUT the FastAPI app, so the unit tests can
drive the whole protocol against a fake transport:

* :mod:`daemon.services.mcps_gateway.protocol` — the wire vocabulary
* :mod:`daemon.services.mcps_gateway.config` — the knobs and the kill switch
* :mod:`daemon.services.mcps_gateway.store` — the machine roster
* :mod:`daemon.services.mcps_gateway.sessions` — the session manager
* :mod:`daemon.services.mcps_gateway.tombstones` — the ended sessions and the reason vocabulary
* :mod:`daemon.services.mcps_gateway.audit` — the remote-command audit
* :mod:`daemon.services.mcps_gateway.service` — the center itself
"""

from .audit import AuditRecord, AuditSink, MemoryAuditSink, NullAuditSink, hash_args
from .config import GatewayConfig, gateway_enabled, load_gateway_config
from .protocol import (
    PROTOCOL_VERSION,
    GatewayAckTimeout,
    GatewayProtocolError,
)
from .service import ClientConnection, GatewayTransport, McpGatewayService
from .sessions import GatewaySession, SessionManager
from .store import InMemoryMachineStore, MachineRecord, MachineStore
from .tombstones import (
    LEDGER_MACHINE_TIMEOUT,
    LEDGER_MACHINE_UNREACHABLE,
    REASON_ALREADY_ENDED,
    REASON_EXPIRED,
    REASON_MACHINE_REFUSED,
    REASON_UNKNOWN_SESSION,
    RESPONSE_REASONS,
    SessionTombstone,
    TombstoneStore,
)

__all__ = [
    "AuditRecord",
    "AuditSink",
    "ClientConnection",
    "GatewayAckTimeout",
    "GatewayConfig",
    "GatewayProtocolError",
    "GatewaySession",
    "GatewayTransport",
    "InMemoryMachineStore",
    "LEDGER_MACHINE_TIMEOUT",
    "LEDGER_MACHINE_UNREACHABLE",
    "MachineRecord",
    "MachineStore",
    "McpGatewayService",
    "MemoryAuditSink",
    "NullAuditSink",
    "PROTOCOL_VERSION",
    "REASON_ALREADY_ENDED",
    "REASON_EXPIRED",
    "REASON_MACHINE_REFUSED",
    "REASON_UNKNOWN_SESSION",
    "RESPONSE_REASONS",
    "SessionManager",
    "SessionTombstone",
    "TombstoneStore",
    "gateway_enabled",
    "hash_args",
    "load_gateway_config",
]
