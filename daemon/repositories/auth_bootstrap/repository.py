"""Auth-bootstrap-consumption repository — oauth2-auth P4 (T4.1 / F10).

One-shot bootstrap mint sentinel store. Contract (phase4-plan T4.1
AMEND v1.3 / F10, quoted semantics):

* ``get_consumption(id_hash)`` — the PRE-HONOR check: a sentinel row
  for ``sha256(env_value)`` means the bootstrap material is BURNED;
  the CLI refuses with the row's ``consumed_at`` / ``actor_label``
  detail.
* ``consume(id_hash, actor_label, consumed_by_pid)`` — the arbitration
  point: INSERT the sentinel row. The PK (``id_hash``) makes a second
  insert of the same material fail at the storage layer (IntegrityError
  → :class:`BootstrapConsumed`), so two concurrent first-mints cannot
  both win — exactly-once by construction, mirroring the state-CAS
  discipline (D2.3).
* The MINT transaction itself (bearer row + sentinel row) is atomic in
  the CLI: both inserts share ONE :class:`sqlmodel.Session` / commit
  against the shared engine — single TX per F10.

Hash-only discipline (NFR-13): the plaintext env value NEVER reaches
this module — callers pass ``sha256(env_value)`` hex.
"""

from __future__ import annotations

import logging
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from daemon.repositories.auth_session.models import AuthBootstrapConsumption

logger = logging.getLogger(__name__)


class BootstrapConsumed(Exception):
    """The bootstrap material was already consumed (sentinel exists).

    Carries the audit detail the CLI surfaces verbatim:
    ``consumed_at`` + ``actor_label`` (+ pid when recorded).
    """

    def __init__(
        self,
        id_hash: str,
        *,
        consumed_at: str,
        actor_label: str,
        consumed_by_pid: int | None = None,
    ) -> None:
        self.id_hash = id_hash
        self.consumed_at = consumed_at
        self.actor_label = actor_label
        self.consumed_by_pid = consumed_by_pid
        super().__init__(
            f"bootstrap material already consumed at {consumed_at} "
            f"for {actor_label}"
        )


class AuthBootstrapRepository:
    """Sentinel-row store over ``auth_bootstrap_consumption``.

    Engine-positional constructor (the shared-engine project standard —
    the SAME engine the daemon's auth tables live on; the CLI resolves
    it via the production DB chain).
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_consumption(self, id_hash: str) -> AuthBootstrapConsumption | None:
        """The sentinel row for ``id_hash``, or ``None`` (not consumed)."""
        with Session(self._engine) as session:
            return session.get(AuthBootstrapConsumption, id_hash)

    def consume(
        self,
        id_hash: str,
        *,
        actor_label: str,
        consumed_at: str,
        consumed_by_pid: int | None = None,
    ) -> None:
        """Insert the sentinel row — the exactly-once arbitration point.

        Raises :class:`BootstrapConsumed` when the row already exists
        (storage-level PK verdict; the exception carries the existing
        row's audit detail). Standalone-committing form; the CLI's
        first mint uses :func:`mint_with_sentinel` instead so the mint
        and the sentinel share ONE transaction (F10).
        """
        session = Session(self._engine)
        try:
            session.add(
                AuthBootstrapConsumption(
                    id_hash=id_hash,
                    consumed_at=consumed_at,
                    actor_label=actor_label,
                    consumed_by_pid=consumed_by_pid,
                )
            )
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            existing = self.get_consumption(id_hash)
            if existing is not None:
                raise BootstrapConsumed(
                    id_hash,
                    consumed_at=existing.consumed_at,
                    actor_label=existing.actor_label,
                    consumed_by_pid=existing.consumed_by_pid,
                ) from exc
            raise
        finally:
            session.close()


class DuplicateTokenMaterial(Exception):
    """The exact token material (by sha256) already has a bearer row.

    A re-mint of identical material is refused even when the existing
    row is expired/revoked — the credential is burned either way and
    silent re-minting would mask rotation. Carries the row state for
    the CLI's error line.
    """

    def __init__(self, id_hash: str, *, revoked_at: str | None, expires_at: str) -> None:
        self.id_hash = id_hash
        self.revoked_at = revoked_at
        self.expires_at = expires_at
        super().__init__(f"token material already minted ({id_hash[:12]}…)")


def mint_with_sentinel(
    engine: Engine,
    *,
    bearer_row: "BearerToken",
    sentinel_id_hash: str,
    actor_label: str,
    consumed_at: str,
    consumed_by_pid: int | None,
) -> None:
    """The F10 atomic first-mint: bearer row + sentinel row, ONE TX.

    Ordering inside the transaction (single Session, single commit):

    1. duplicate-material guard — a bearer row with the same
       ``id_hash`` (any state) ⇒ :class:`DuplicateTokenMaterial`;
    2. one-shot guard — a sentinel row for ``sentinel_id_hash`` ⇒
       :class:`BootstrapConsumed` (with the stored audit detail);
    3. INSERT bearer + INSERT sentinel → ``commit()``.

    A concurrent minter losing the PK race lands in the
    ``IntegrityError`` handler, re-reads the stored rows, and surfaces
    the same typed errors — exactly-once by storage constraint, not by
    check-then-insert hope.
    """
    from daemon.repositories.auth_session.models import BearerToken

    session = Session(engine)
    try:
        existing_token = session.get(BearerToken, bearer_row.id_hash)
        if existing_token is not None:
            raise DuplicateTokenMaterial(
                bearer_row.id_hash,
                revoked_at=existing_token.revoked_at,
                expires_at=existing_token.expires_at,
            )
        existing_sentinel = session.get(AuthBootstrapConsumption, sentinel_id_hash)
        if existing_sentinel is not None:
            raise BootstrapConsumed(
                sentinel_id_hash,
                consumed_at=existing_sentinel.consumed_at,
                actor_label=existing_sentinel.actor_label,
                consumed_by_pid=existing_sentinel.consumed_by_pid,
            )
        session.add(bearer_row)
        session.add(
            AuthBootstrapConsumption(
                id_hash=sentinel_id_hash,
                consumed_at=consumed_at,
                actor_label=actor_label,
                consumed_by_pid=consumed_by_pid,
            )
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        # The PK race: re-read whichever row won and surface its typed
        # error with the stored detail (sentinel wins the error order —
        # the one-shot contract is the operator-facing message).
        winner_sentinel = session.get(AuthBootstrapConsumption, sentinel_id_hash)
        if winner_sentinel is not None:
            raise BootstrapConsumed(
                sentinel_id_hash,
                consumed_at=winner_sentinel.consumed_at,
                actor_label=winner_sentinel.actor_label,
                consumed_by_pid=winner_sentinel.consumed_by_pid,
            ) from exc
        winner_token = session.get(BearerToken, bearer_row.id_hash)
        if winner_token is not None:
            raise DuplicateTokenMaterial(
                bearer_row.id_hash,
                revoked_at=winner_token.revoked_at,
                expires_at=winner_token.expires_at,
            ) from exc
        raise
    finally:
        session.close()


__all__ = [
    "AuthBootstrapRepository",
    "BootstrapConsumed",
    "DuplicateTokenMaterial",
    "mint_with_sentinel",
]


__all__ = ["AuthBootstrapRepository", "BootstrapConsumed"]
