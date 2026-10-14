"""Custom-agent config repository package (Phase C of the
agent-prompt-immutability commission).

User Directive #5A (2026-10-14, overnight, R-A1/A2/B1-B4 lifecycle).
The package exposes:

* :mod:`daemon.repositories.custom_agent_configs.models` — the
  :class:`CustomAgentConfig` SQLModel declaration + the
  :data:`CUSTOM_AGENT_ID_PREFIX` namespace constant +
  :func:`make_custom_agent_id` / :func:`is_custom_agent_id` wire-
  format helpers. Byte-identical index / unique-constraint names
  to the ``.sql`` migration
  (``daemon/migrations/versions/20261014_040000_create_custom_agent_configs.sql``)
  per the dual-driver parity contract — the model is the
  canonical source for the PG ``create_all`` path.
* :mod:`daemon.repositories.custom_agent_configs.repository` —
  :class:`CustomAgentConfigRepository` sync class implementing the
  insertion / lookup / soft-delete / restore surface that the
  router + registry use.

The Phase C REST endpoints (``/api/custom-agents`` POST + DELETE)
sit on top of this repo. Phase D (frontend list / create / delete
UI per the existing ``skill-bank.service.ts`` pattern) and
phase E (per-plugin custom-agent templates — out of scope for
this overnight) follow.
"""

from __future__ import annotations

from .models import (
    CUSTOM_AGENT_ID_PREFIX,
    CustomAgentConfig,
    is_custom_agent_id,
    make_custom_agent_id,
)
from .repository import (
    CustomAgentCollisionError,
    CustomAgentConfigNotFound,
    CustomAgentConfigRepository,
    CustomAgentIdError,
)

__all__ = [
    "CustomAgentConfig",
    "CustomAgentConfigRepository",
    "CustomAgentIdError",
    "CustomAgentCollisionError",
    "CustomAgentConfigNotFound",
    "CUSTOM_AGENT_ID_PREFIX",
    "is_custom_agent_id",
    "make_custom_agent_id",
]
