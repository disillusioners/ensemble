"""Agent-secrets repository — durable Fernet-encrypted secret store.

Phase 1 of the agent-secrets commission
(``.agents/shared/planning/agent-secrets/``). The package owns
the SQLModel tables + the action-enum constants that back the
in-process :class:`SecretService` (``daemon/services/secret_service.py``).

The split mirrors the sibling repositories under
``daemon/repositories/`` — ``__init__`` re-exports the
public surface so callers can write
``from daemon.repositories.secrets import Secret`` instead of
reaching into ``models``.

Public surface
--------------

* :class:`Secret` — the SQLModel table for stored secrets
  (durable Fernet-encrypted row backing the new ``secrets``
  privileged tool category, #7 in ``PRIVILEGED_TOOL_CATEGORIES``).
* :class:`SecretAuditEvent` — the SQLModel append-only audit
  log stamped inside the same ``engine.begin()`` transaction
  as each mutation (M1 atomicity contract, governor
  pre-promote fix 2026-10-17).
* :data:`SECRET_AUDIT_ACTIONS` — frozen set of all valid
  action tags (``create`` / ``read_public`` / ``retire`` +
  ``use_marker`` / ``read_value`` reserved-for-P2). The
  service boundary validates incoming actions against this
  set; the model is a dumb store.
* :data:`SECRET_AUDIT_ACTIONS_P1` — the P1-emitter subset
  (excludes ``use_marker`` / ``read_value``; reserved for
  the P2 marker-resolver audit row and a future value-read
  tool). Pinned by the test suite to assert the P1/P2
  partition.

The migration lives at
``daemon/migrations/versions/20261017_000001_create_secrets_tables.sql``.
The package has no ``repository.py`` of its own (28/28
sibling packages have one) — the storage seam is a thin
port in ``daemon/services/secret_service.py`` rather
than a full repository class, per plan §1.3 (port-shaped
seam — ``_SecretStorePort``). The P1 storage seam keeps
the package intentional: tables + constants only, no
repository abstraction on top of SQLAlchemy.
"""

from __future__ import annotations

from .models import (
    SECRET_AUDIT_ACTIONS,
    SECRET_AUDIT_ACTIONS_P1,
    Secret,
    SecretAuditEvent,
)

__all__ = [
    "SECRET_AUDIT_ACTIONS",
    "SECRET_AUDIT_ACTIONS_P1",
    "Secret",
    "SecretAuditEvent",
]
