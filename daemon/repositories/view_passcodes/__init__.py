"""Agent view-passcodes repository — durable Fernet-encrypted passcode store.

Phase 1 of the live-view-passcode commission
(``.agents/shared/planning/live-view-passcode/``). The package owns
the SQLModel tables + the domain constants that back the in-process
:class:`ViewPasscodeService` (``daemon/services/view_passcode_service.py``).

The split mirrors the sibling repositories under
``daemon/repositories/`` — ``__init__`` re-exports the public
surface so callers can write
``from daemon.repositories.view_passcodes import LiveViewPasscode``
instead of reaching into ``models``.

Public surface
--------------

* :class:`LiveViewPasscode` — the SQLModel table for stored
  passcodes (durable Fernet-encrypted row backing the P2
  ``/views/public/{root}/{rel}`` family).
* :class:`LiveViewPasscodeAudit` — the SQLModel append-only
  audit log stamped inside the same ``engine.begin()`` transaction
  as each mutation (M1 atomicity contract, the secrets precedent).
* :data:`LIVE_VIEW_AUDIT_ACTIONS` — frozen set of the closed audit
  action values (``mint`` / ``regenerate`` / ``grant`` / ``deny``
  / ``lockout`` per ADR D9:115). The service boundary validates
  incoming actions against this set; the model is a dumb store.
* :data:`LIVE_VIEW_PASSCODE_ALPHABET` — the 31-symbol unambiguous
  alphabet (ADR D4 — drops 0/O/1/i/l).
* :data:`LIVE_VIEW_PASSCODE_LENGTH` — the fixed 6-character length
  (ADR D4).
* :data:`LIVE_VIEW_PASSCODE_STRIKE_CEILING` — the 10th-wrong-
  verify flip point (ADR D5).
* :data:`LIVE_VIEW_AUDIT_IP_KDF_INFO` — the domain-separation
  info string for the IP-HMAC key derivation (ADR D9:116).

The migration lives at
``daemon/migrations/versions/20261017_193816_create_live_view_passcode_tables.sql``.
The package has no ``repository.py`` of its own — the storage
seam is a thin port in ``daemon/services/view_passcode_service.py``
rather than a full repository class, per the secrets precedent
(``daemon/services/secret_service.py:_SecretStorePort``). The P1
storage seam keeps the package intentional: tables + constants
only, no repository abstraction on top of SQLAlchemy.
"""

from __future__ import annotations

from .models import (
    LIVE_VIEW_AUDIT_ACTIONS,
    LIVE_VIEW_AUDIT_IP_KDF_INFO,
    LIVE_VIEW_PASSCODE_ALPHABET,
    LIVE_VIEW_PASSCODE_LENGTH,
    LIVE_VIEW_PASSCODE_STRIKE_CEILING,
    LiveViewPasscode,
    LiveViewPasscodeAudit,
)

__all__ = [
    "LIVE_VIEW_AUDIT_ACTIONS",
    "LIVE_VIEW_AUDIT_IP_KDF_INFO",
    "LIVE_VIEW_PASSCODE_ALPHABET",
    "LIVE_VIEW_PASSCODE_LENGTH",
    "LIVE_VIEW_PASSCODE_STRIKE_CEILING",
    "LiveViewPasscode",
    "LiveViewPasscodeAudit",
]