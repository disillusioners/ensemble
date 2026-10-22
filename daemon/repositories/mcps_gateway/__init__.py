"""The ``mcp_gateway_audit`` table — the remote-command audit ledger.

One row per forwarded remote command: who / machine / tool / args-hash /
outcome (§8.4). The arguments are HASHED and never stored — the table
has no arguments column at all.
"""

from .models import McpGatewayAudit
from .repository import McpGatewayAuditRepository

__all__ = ["McpGatewayAudit", "McpGatewayAuditRepository"]
