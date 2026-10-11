"""Auth-session repository — oauth2-auth Phase 2 (T2.3).

Synchronous repository over the four auth tables (engine-positional
constructor, shared-engine discipline — the project standard; callers
bridge to async via ``asyncio.to_thread``, e.g. the phase-2 sweeper).

Contracts implemented here
--------------------------
* **Session CRUD + batched touch + soft-invalidate**:
  ``insert_session`` / ``get_session`` / ``touch_sessions`` (single
  UPDATE gated on a 60 s min-interval — "1m < last_used_at"-batched per
  [TA] §Session Model, avoids a write per request) /
  ``invalidate_session`` (row DELETE — [TA] §Logout: "Single-CAS
  ``DELETE FROM auth_sessions WHERE id=?``"; the ``auth_sessions``
  schema has NO ``revoked_at`` column — soft revocation is the BEARER
  table's mechanism).
* **State exactly-once CAS (D2.3)**: :meth:`AuthSessionRepository.consume_state`
  is a SINGLE portable conditional UPDATE
  (``SET consumed_at=:now WHERE id=:id AND consumed_at IS NULL``) —
  the exactly-once arbitration point. No SELECT-then-UPDATE race
  window: the rowcount IS the verdict. Outcome mapping (the P3 callback
  turns these into HTTP 400s):
  - rowcount 1 + unexpired  → ``SUCCESS``
  - rowcount 1 + expired    → ``EXPIRED`` (row left consumed — the
    replay window is dead either way; expired dominates)
  - rowcount 0, row absent  → ``NOT_FOUND``
  - rowcount 0, row expired → ``EXPIRED``
  - rowcount 0, consumed    → ``REPLAYED`` (→ 400 STATE_REPLAYED)
* **Bearer hash-only storage**: insert/get-by-hash/revoke; lookups skip
  revoked and expired rows.
* **Batched sweep (D2.4 + AMEND v1.3 / F2)**: the session/token sweeps
  page in batches of 1000 via
  ``SELECT id … WHERE expires_at < :cutoff LIMIT :n
  .with_for_update(skip_locked=True)`` + ``DELETE … WHERE id IN (...)``
  inside ONE short transaction PER BATCH — a connection is never held
  across batches (2026-10-10 checkpoint-pool incident sensitivity).
  The ``with_for_update(skip_locked=True)`` clause is SQLAlchemy's
  DIALECT-AGNOSTIC spelling: PostgreSQL renders ``FOR UPDATE SKIP
  LOCKED`` (concurrent sweepers skip each other's rows); SQLite
  silently no-ops the clause, so the SAME code path executes a plain
  batched SELECT+DELETE (the portable fallback). NO runtime dialect
  branch exists anywhere in this module (D17 single code path); the
  dual shape is pinned by ``tests/unit/test_auth_sweeper.py`` +
  ``tests/postgres`` runs (G2.1/F2).
* **State sweep (60 s loop)**: deletes rows that are consumed (any age)
  OR expired — live (pending, unexpired) states are never touched.

Every method opens its own short-lived :class:`sqlmodel.Session` — no
connection reuse across calls, no long-lived transaction.
"""

from __future__ import annotations

import enum
import logging
from typing import Sequence

from sqlalchemy import delete, update
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from .models import AuthSession, AuthState, BearerToken

logger = logging.getLogger(__name__)

#: [TA] §Session Model — last-used updates are "1m < last_used_at"-batched
#: to avoid a write per request.
TOUCH_MIN_INTERVAL_SECONDS = 60


class StateConsumeOutcome(str, enum.Enum):
    """Outcome of the exactly-once state CAS (D2.3).

    The P3 callback maps ``REPLAYED`` → ``400 STATE_REPLAYED`` and
    ``EXPIRED`` → ``400 STATE_EXPIRED``.
    """

    SUCCESS = "success"
    REPLAYED = "replayed"
    EXPIRED = "expired"
    NOT_FOUND = "not_found"


class AuthSessionRepository:
    """CRUD + CAS + batched sweep over the four auth tables.

    Constructor takes a SQLAlchemy ``Engine`` positionally (repo-wide
    convention — ``tests/postgres/conftest.py::pg_repository_factory``
    shape). All methods are synchronous; async callers bridge via
    ``asyncio.to_thread``.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ── Sessions ────────────────────────────────────────────────────

    def insert_session(self, row: AuthSession) -> None:
        """Insert one session row (idempotence NOT attempted — the
        caller mints a fresh random id per session)."""
        with Session(self._engine) as session:
            session.add(row)
            session.commit()

    def get_session(self, session_id: str) -> AuthSession | None:
        """Return the session row by id, or ``None``."""
        with Session(self._engine) as session:
            return session.get(AuthSession, session_id)

    def touch_sessions(
        self,
        session_ids: Sequence[str],
        *,
        now_iso: str,
        min_interval_seconds: int = TOUCH_MIN_INTERVAL_SECONDS,
    ) -> int:
        """Batched ``last_used_at`` touch.

        Single UPDATE over the id batch, gated on the min-interval so
        hot validate paths do not write per request: rows already
        touched within ``min_interval_seconds`` of ``now_iso`` are
        skipped by the WHERE clause (ISO strings compare
        chronologically). Returns the number of rows updated.

        The floor iso is computed by the CALLER (this module stays
        clock-free for testability).
        """
        if not session_ids:
            return 0
        floor_iso = _iso_minus_seconds(now_iso, min_interval_seconds)
        stmt = (
            update(AuthSession)
            .where(
                col(AuthSession.id).in_(list(session_ids)),
                col(AuthSession.last_used_at).is_(None)
                | (col(AuthSession.last_used_at) < floor_iso),
            )
            .values(last_used_at=now_iso)
        )
        with Session(self._engine) as session:
            result = session.exec(stmt)
            session.commit()
            return result.rowcount or 0

    def invalidate_session(self, session_id: str) -> bool:
        """Logout: DELETE the session row ([TA] §Logout single-CAS
        delete). Returns True when a row was removed."""
        stmt = delete(AuthSession).where(col(AuthSession.id) == session_id)
        with Session(self._engine) as session:
            result = session.exec(stmt)
            session.commit()
            return result.rowcount > 0

    # ── States (exactly-once CAS) ───────────────────────────────────

    def insert_state(self, row: AuthState) -> None:
        """Insert one pending state row before redirecting to the IdP."""
        with Session(self._engine) as session:
            session.add(row)
            session.commit()

    def get_state(self, state_id: str) -> AuthState | None:
        """Return the state row by id, or ``None``."""
        with Session(self._engine) as session:
            return session.get(AuthState, state_id)

    def consume_state(
        self,
        state_id: str,
        *,
        now: object,
        now_iso: str,
    ) -> StateConsumeOutcome:
        """Exactly-once state consumption — the single CAS (D2.3).

        One portable conditional UPDATE carries the whole arbitration:
        ``UPDATE auth_states SET consumed_at=:now WHERE id=:id AND
        consumed_at IS NULL``. ``rowcount`` decides:

        - 1 → this caller won. If the row is already past
          ``expires_at`` → ``EXPIRED`` (the row REMAINS consumed —
          a consumed expired state is unreplayable either way);
          otherwise ``SUCCESS``.
        - 0 → someone else consumed it first (or it never existed).
          Re-read to discriminate: absent → ``NOT_FOUND``; expired →
          ``EXPIRED``; consumed → ``REPLAYED``.

        ``now``/``now_iso`` are caller-supplied (same instant in both
        shapes) so tests inject a clock. NOTE: the portability claim
        (identical PG/SQLite semantics, no dialect branch) is the
        point — no ``SELECT … FOR UPDATE`` is involved.
        """
        stmt = (
            update(AuthState)
            .where(
                col(AuthState.id) == state_id,
                col(AuthState.consumed_at).is_(None),
            )
            .values(consumed_at=now_iso)
        )
        with Session(self._engine) as session:
            result = session.exec(stmt)
            session.commit()
            won = result.rowcount == 1

        if won:
            row = self.get_state(state_id)
            if row is not None and row.expires_at < now_iso:
                return StateConsumeOutcome.EXPIRED
            return StateConsumeOutcome.SUCCESS

        row = self.get_state(state_id)
        if row is None:
            return StateConsumeOutcome.NOT_FOUND
        if row.expires_at < now_iso:
            return StateConsumeOutcome.EXPIRED
        return StateConsumeOutcome.REPLAYED

    def sweep_expired_states(self, *, cutoff_iso: str) -> int:
        """Delete states that are consumed (any age) OR expired.

        The 60 s state-loop sweeper's payload. Live rows (pending,
        unexpired) are NEVER touched. Returns rows deleted.
        """
        stmt = delete(AuthState).where(
            col(AuthState.consumed_at).is_not(None)
            | (col(AuthState.expires_at) < cutoff_iso)
        )
        with Session(self._engine) as session:
            result = session.exec(stmt)
            session.commit()
            return result.rowcount or 0

    # ── Bearer tokens (hash-only) ───────────────────────────────────

    def insert_bearer(self, row: BearerToken) -> None:
        """Insert one bearer row — ``id_hash`` MUST already be
        ``SHA-256(token)`` hex (this module never sees plaintext)."""
        with Session(self._engine) as session:
            session.add(row)
            session.commit()

    def get_bearer_by_hash(
        self,
        id_hash: str,
        *,
        now_iso: str,
    ) -> BearerToken | None:
        """Lookup by ``SHA-256(token)``; revoked and expired rows are
        skipped (return ``None``)."""
        row = None
        with Session(self._engine) as session:
            row = session.get(BearerToken, id_hash)
        if row is None:
            return None
        if row.revoked_at is not None:
            return None
        if row.expires_at < now_iso:
            return None
        return row

    def revoke_bearer(self, id_hash: str, *, now_iso: str) -> bool:
        """Soft revoke: set ``revoked_at`` once (idempotent — an
        already-revoked row keeps its FIRST revocation timestamp).
        Returns True when this call performed the revocation."""
        stmt = (
            update(BearerToken)
            .where(
                col(BearerToken.id_hash) == id_hash,
                col(BearerToken.revoked_at).is_(None),
            )
            .values(revoked_at=now_iso)
        )
        with Session(self._engine) as session:
            result = session.exec(stmt)
            session.commit()
            return result.rowcount == 1

    # ── Batched sweeps (D2.4 + F2 dialect-agnostic) ─────────────────

    def _sweep_expired_batched(
        self,
        model: type[AuthSession] | type[BearerToken],
        *,
        cutoff_iso: str,
        batch_size: int,
    ) -> int:
        """Shared batched sweep body for ``auth_sessions`` and
        ``auth_bearer_tokens``.

        Per batch: ONE short transaction —
        ``SELECT id WHERE expires_at < :cutoff LIMIT :n
        .with_for_update(skip_locked=True)`` then
        ``DELETE WHERE id IN (batch)`` then COMMIT. The connection is
        acquired and released PER BATCH (never held across the sweep —
        2026-10-10 checkpoint-pool incident sensitivity).

        AMEND v1.3 / F2: ``with_for_update(skip_locked=True)`` is the
        dialect-agnostic SQLAlchemy spelling — PostgreSQL renders
        ``FOR UPDATE SKIP LOCKED`` (concurrent sweepers skip each
        other's locked rows); SQLite silently no-ops the clause and the
        identical code path degrades to a plain batched SELECT+DELETE.
        No runtime dialect branch exists (D17 single code path).
        """
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")

        # The shared body serves tables with DIFFERENT PK attribute
        # names (auth_sessions.id vs auth_bearer_tokens.id_hash) —
        # resolve the actual PK column from the table metadata.
        pk_col = list(model.__table__.primary_key.columns)[0]

        deleted_total = 0
        while True:
            with Session(self._engine) as session:
                id_select = (
                    select(pk_col)
                    .where(col(model.expires_at) < cutoff_iso)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
                ids = list(session.exec(id_select).all())
                if not ids:
                    break
                delete_stmt = delete(model).where(pk_col.in_(ids))
                result = session.exec(delete_stmt)
                session.commit()
                deleted = result.rowcount or 0
            deleted_total += deleted
            if len(ids) < batch_size:
                break
            # Full batch deleted — loop for the next page. The session
            # above is CLOSED before this next iteration opens a fresh
            # one (per-batch connection acquisition).
        return deleted_total

    def sweep_expired_sessions(
        self,
        *,
        cutoff_iso: str,
        batch_size: int = 1000,
    ) -> int:
        """Delete expired session rows in batches; returns rows deleted."""
        return self._sweep_expired_batched(
            AuthSession, cutoff_iso=cutoff_iso, batch_size=batch_size
        )

    def sweep_expired_bearer_tokens(
        self,
        *,
        cutoff_iso: str,
        batch_size: int = 1000,
    ) -> int:
        """Delete expired bearer rows in batches; returns rows deleted.

        Revoked-but-unexpired rows are intentionally RETAINED until
        their natural expiry (the revocation audit window) — the sweep
        keys on ``expires_at`` only.
        """
        return self._sweep_expired_batched(
            BearerToken, cutoff_iso=cutoff_iso, batch_size=batch_size
        )


def _iso_minus_seconds(iso: str, seconds: int) -> str:
    """Return ``iso`` shifted back by ``seconds``, same canonical format.

    Works on the ``now_utc_iso()`` shape (aware ISO-8601 with
    microseconds + ``+00:00``). Parsing keeps the format stable so the
    string comparison in the touch WHERE clause stays chronological.
    """
    from datetime import datetime, timedelta, timezone

    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    shifted = dt - timedelta(seconds=seconds)
    return shifted.isoformat()


__all__ = [
    "AuthSessionRepository",
    "StateConsumeOutcome",
    "TOUCH_MIN_INTERVAL_SECONDS",
]
