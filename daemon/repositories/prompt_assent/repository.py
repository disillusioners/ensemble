"""The ``prompt_assent_tokens`` repository — mint / find / consume / counters / sweep.

Every method is a synchronous primitive that opens its OWN transaction
(``engine.begin()`` / ``Session``) and commits — the ``notify_audit``
writer precedent. Callers (the P2 tool gate / P3 assent service) own the
fail-closed conversion of any raise into typed refusals.

D-4 BLOCKING RULE (the one this module exists to uphold)
--------------------------------------------------------
``record_failed_attempt`` — the attempt-counter increment AND the
lockout revoke at threshold — commits in its OWN transaction. It is
called on the REFUSAL PATH ONLY, BEFORE any guarded transition opens.
It must never share the guarded transition's transaction: a rollback
inside that transaction would reset the counter and the lockout would
never fire (D-3's defense-in-depth silently inert). The property is
DIRECTLY ASSERTED in ``tests/postgres/test_prompt_assent_token_store_pg.py``
(the counter survives a forced rollback of a neighbouring transaction).

TRANSITION-THEN-CONSUME
-----------------------
Refusals never burn the token. The consume is co-transactional with the
state change via the D-4 keyword-only hook on ``atomic_transition`` —
the in-transaction variant is :meth:`consume_in_conn` (drives the hook
built by :mod:`daemon.repositories.prompt_assent.consume_hook`). The
standalone :meth:`consume` (own transaction) exists for tests and
non-transition administrative consumes.

GC DISCIPLINE (mirrors the promote GC — ``upgrade_journal.py``)
---------------------------------------------------------------
``sweep`` only deletes what it can prove is dead: rows whose TTL has
passed AND that are not ``consumed``. Consumed rows are RETAINED
unconditionally so ``token-already-used`` stays answerable forever (the
"keeps one consumed entry" rule, applied per-token: every consumed row
stays). Superseded/revoked rows within TTL are also retained so a
replayed token reads its precise status; past TTL they are provably dead
and swept.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Engine, delete
from sqlmodel import Session, col, select

from .models import (
    PROMPT_ASSENT_TOKENS_TABLE,
    STATUS_CONSUMED,
    STATUS_LIVE,
    STATUS_REVOKED,
    STATUS_SUPERSEDED,
    TOKEN_STATUSES,
    PromptAssentToken,
    token_ttl_expires_at,
)

#: Default per-token mismatch threshold (D-4 / R-3): at the 5th recorded
#: mismatch the live token is revoked — a fresh mint (⇒ a fresh human
#: turn) is required to clear it. The threshold is a PARAMETER, not a
#: hard-coded constant: the P4 config class passes the configured value.
REVOKE_THRESHOLD_DEFAULT = 5


class AssentTokenError(Exception):
    """A token-store refusal, carrying the taxonomy code (ruling §3).

    ``code`` is one of the ruled typed refusal codes (``mint-live-token-
    exists``, ``token-already-used``, ``token-superseded``, ``token-
    mismatch``, ``token-item-mismatch``, ``token-expired``). The tool
    gate maps it onto the refusal envelope; the store never invents a
    code outside the taxonomy.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _lock_key_assent(token_id: str) -> int:
    """Advisory-lock key for one token's attempt counter (R3 analogue).

    63-bit non-negative int from a length-prefixed-canonical sha256 of
    ``token_id`` — the same derivation discipline as the prompt-version
    ``_lock_key`` (``repository.py:162``); the ``"prompt-assent\\x1f"``
    prefix namespaces the key-space so the two lanes' advisory locks can
    never collide by accident.
    """
    payload = b"prompt-assent\x1f" + str(token_id).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") & (
        (1 << 63) - 1
    )


class PromptAssentTokenRepository:
    """Writer + readers for the ``prompt_assent_tokens`` table.

    Constructed with the daemon engine (the same engine the prompt-version
    repository uses). Hash-only by construction: no method accepts or
    returns a raw token — callers hash before calling (the mint response
    is the raw token's only appearance).

    ``upgrade_journal`` clock helpers (``now_iso`` / ``parse_iso_utc``)
    are imported INSIDE the methods that use them — a module-level
    ``daemon.tools`` import from this package re-enters
    ``daemon.tools.__init__`` → ``instance.py`` → ``repositories.factory``
    and dead-locks the boot import order (the deferred-import rule the
    model module also follows).
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ── mint ─────────────────────────────────────────────────────────────

    def mint(
        self,
        *,
        version_id: str,
        agent_id: str,
        version_tag: str,
        artifact: str,
        version: int,
        token_hash: str,
        render_sha256: str,
        mint_source_id: Optional[str] = None,
        mint_channel_id: Optional[str] = None,
        mint_is_dm: bool = False,
        mint_user_id: Optional[str] = None,
        minted_external_user_id: Optional[str] = None,
        force: bool = False,
    ) -> PromptAssentToken:
        """Insert one ``live`` token row — own transaction.

        A re-mint **supersedes AND revokes** the prior live token for the
        same item: the prior row flips ``status='superseded'`` +
        ``superseded_at`` in the SAME transaction as the new INSERT (the
        partial unique index makes the flip-then-insert ordering
        structural — a second live row for one item cannot exist). The
        prior token then reads ``token-superseded`` — deliberately
        distinct from ``token-already-used`` (S-2).

        ``force=False`` (default) refuses when a live token already
        exists for the item (typed ``mint-live-token-exists`` — ruling
        M-4b); the tool gate checks first and passes its own force
        decision through. ``force=True`` performs the supersede flip and
        inserts.

        The TTL is ``issued_at + NONCE_TTL_S`` (imported SSOT, 60 min).
        NO audit event is written — the token table IS the mint ledger
        (D-9).

        Returns the persisted row (fresh read).
        """
        if token_hash.lower() != token_hash or len(token_hash) != 64:
            raise ValueError(
                "mint: token_hash must be lower-case sha256 hex (64 chars) "
                "— NEVER the raw token"
            )
        if render_sha256.lower() != render_sha256 or len(render_sha256) != 64:
            raise ValueError(
                "mint: render_sha256 must be lower-case sha256 hex "
                "(64 chars) of the canonical render (J-3/K-7)"
            )
        from daemon.tools.upgrade_journal import now_iso

        issued_at = now_iso()
        with self._engine.begin() as conn:
            prior_live = conn.execute(
                select(col(PromptAssentToken.token_id))
                .where(col(PromptAssentToken.version_id) == version_id)
                .where(col(PromptAssentToken.status) == STATUS_LIVE)
            ).first()
            if prior_live is not None and not force:
                raise AssentTokenError(
                    "mint-live-token-exists",
                    f"a live token already exists for version_id="
                    f"{version_id}; re-mint requires force (M-4b)",
                )
            if prior_live is not None:
                # Supersede flip — SAME transaction as the INSERT (the
                # partial unique index demands the ordering).
                conn.execute(
                    PromptAssentToken.__table__.update()
                    .where(col(PromptAssentToken.token_id) == prior_live[0])
                    .where(col(PromptAssentToken.status) == STATUS_LIVE)
                    .values(status=STATUS_SUPERSEDED, superseded_at=_now())
                )
            row = PromptAssentToken(
                version_id=version_id,
                agent_id=agent_id,
                version_tag=version_tag,
                artifact=artifact,
                version=version,
                token_hash=token_hash,
                render_sha256=render_sha256,
                mint_source_id=mint_source_id,
                mint_channel_id=mint_channel_id,
                mint_is_dm=1 if mint_is_dm else 0,
                mint_user_id=mint_user_id,
                minted_external_user_id=minted_external_user_id,
                issued_at=issued_at,
                ttl_expires_at=token_ttl_expires_at(issued_at),
                status=STATUS_LIVE,
            )
            conn.execute(PromptAssentToken.__table__.insert().values(**_row_values(row)))
        return self._must_get(row.token_id)

    # ── finders ──────────────────────────────────────────────────────────

    def find_live(self, version_id: str) -> Optional[PromptAssentToken]:
        """The live token for an item, or ``None``."""
        with Session(self._engine) as session:
            row = session.exec(
                select(PromptAssentToken)
                .where(col(PromptAssentToken.version_id) == version_id)
                .where(col(PromptAssentToken.status) == STATUS_LIVE)
            ).first()
            return row

    def find_by_hash(self, token_hash: str) -> Optional[PromptAssentToken]:
        """The token row for a hash (any status), or ``None``."""
        with Session(self._engine) as session:
            return session.exec(
                select(PromptAssentToken).where(
                    col(PromptAssentToken.token_hash) == token_hash
                )
            ).first()

    def find_by_id(self, token_id: str) -> Optional[PromptAssentToken]:
        """The token row by PK (any status), or ``None``."""
        with Session(self._engine) as session:
            return session.get(PromptAssentToken, token_id)

    # ── consume (transition-then-consume; D-4) ───────────────────────────

    def consume(
        self,
        token_id: str,
        consumed_by_message_id: str,
    ) -> PromptAssentToken:
        """Consume-CAS — own transaction (administrative path).

        ``UPDATE … SET status='consumed', consumed_at=…,
        consumed_by_message_id=… WHERE token_id=? AND status='live'``;
        rowcount 0 ⇒ typed refusal (the precise status is read back:
        ``token-already-used`` / ``token-superseded`` / ``token-mismatch``).
        The PRODUCTION consume path is the co-transactional
        :meth:`consume_in_conn` inside the D-4 hook; this standalone
        wrapper exists for tests and administrative cleanup.
        """
        with self._engine.begin() as conn:
            self.consume_in_conn(
                conn,
                token_id=token_id,
                consumed_by_message_id=consumed_by_message_id,
            )
        return self._must_get(token_id)

    def consume_in_conn(
        self,
        conn: Any,
        *,
        token_id: str,
        consumed_by_message_id: str,
    ) -> None:
        """Consume-CAS inside the CALLER's transaction (the D-4 hook body).

        Rowcount 0 ⇒ raise ⇒ the caller's transaction rolls back with it
        (the state change + the audit row die together — the token is
        never burned on a non-decision).
        """
        result = conn.execute(
            PromptAssentToken.__table__.update()
            .where(col(PromptAssentToken.token_id) == token_id)
            .where(col(PromptAssentToken.status) == STATUS_LIVE)
            .values(
                status=STATUS_CONSUMED,
                consumed_at=_now(),
                consumed_by_message_id=consumed_by_message_id,
            )
        )
        if result.rowcount == 0:
            raise AssentTokenError(*_not_live_code(conn, token_id))

    # ── attempt counter + lockout (D-4 BLOCKING rule) ────────────────────

    def record_failed_attempt(
        self,
        token_id: str,
        revoke_threshold: int = REVOKE_THRESHOLD_DEFAULT,
    ) -> bool:
        """Increment the per-token mismatch counter — OWN transaction.

        REFUSAL PATH ONLY, BEFORE any guarded transition opens. At the
        threshold the live token is revoked IN THE SAME OWN TRANSACTION
        (counter + lockout commit together or not at all — D-4 blocking
        rule). Returns ``True`` when THIS call revoked the token.

        Because the transaction commits here, a rollback of a NEIGHBOURING
        transaction (the guarded transition) can never reset the counter —
        the property the PG test asserts directly.

        Concurrency (R3 analogue, the prompt-version advisory-lock
        pattern): the increment is a read-modify-write — plain
        SELECT+UPDATE pairs LOSE UPDATES on PG READ COMMITTED (two
        refusals read attempts=N and both write N+1). The transaction
        therefore opens with a dialect-gated ``pg_advisory_xact_lock``
        keyed by ``token_id`` (``_lock_key_assent``); on SQLite it is a
        no-op (database-level single-writer). The lock is
        transaction-scoped: it auto-releases at commit/rollback and
        serializes ONLY writers on the same token.
        """
        with self._engine.begin() as conn:
            if self._engine.dialect.name == "postgresql":
                from sqlalchemy import text as _text

                conn.execute(
                    _text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": _lock_key_assent(token_id)},
                )
            row = conn.execute(
                select(
                    col(PromptAssentToken.attempts),
                    col(PromptAssentToken.status),
                )
                .where(col(PromptAssentToken.token_id) == token_id)
            ).first()
            if row is None:
                raise LookupError(f"assent token {token_id} not found")
            attempts = int(row[0]) + 1
            values: dict[str, Any] = {"attempts": attempts}
            revoked = False
            if attempts >= revoke_threshold and row[1] == STATUS_LIVE:
                values["status"] = STATUS_REVOKED
                revoked = True
            conn.execute(
                PromptAssentToken.__table__.update()
                .where(col(PromptAssentToken.token_id) == token_id)
                .values(**values)
            )
        return revoked

    def revoke(self, token_id: str) -> bool:
        """Revoke a LIVE token — own transaction. ``True`` if revoked.

        Guarded CAS: only a ``live`` row flips (an already-consumed or
        superseded token keeps its precise terminal status — S-2). A
        non-live token is a no-op returning ``False``.
        """
        with self._engine.begin() as conn:
            result = conn.execute(
                PromptAssentToken.__table__.update()
                .where(col(PromptAssentToken.token_id) == token_id)
                .where(col(PromptAssentToken.status) == STATUS_LIVE)
                .values(status=STATUS_REVOKED)
            )
        return bool(result.rowcount)

    # ── TTL sweep (promote GC discipline) ────────────────────────────────

    def sweep(self, now: Optional[datetime] = None) -> int:
        """Delete expired NON-consumed rows; retain consumed rows.

        Only deletes what it can prove is dead: ``ttl_expires_at < now``
        AND ``status != 'consumed'``. Consumed rows survive forever so
        ``token-already-used`` stays answerable (the promote GC's
        "keeps one consumed entry" rule — applied per-token, stronger).
        Returns the number of rows deleted.
        """
        cutoff = (now or datetime.now(timezone.utc)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        with self._engine.begin() as conn:
            result = conn.execute(
                delete(PromptAssentToken).where(
                    col(PromptAssentToken.ttl_expires_at) < cutoff,
                    col(PromptAssentToken.status) != STATUS_CONSUMED,
                )
            )
        return int(result.rowcount)

    def is_expired(self, row_or_expires_at: Any) -> bool:
        """Fail-closed TTL check: unparseable ⇒ expired (never valid)."""
        expires_at = (
            row_or_expires_at
            if isinstance(row_or_expires_at, str)
            else getattr(row_or_expires_at, "ttl_expires_at", None)
        )
        from daemon.tools.upgrade_journal import parse_iso_utc

        parsed = parse_iso_utc(expires_at)
        if parsed is None:
            return True
        return parsed <= datetime.now(timezone.utc)

    # ── internals ────────────────────────────────────────────────────────

    def _must_get(self, token_id: str) -> PromptAssentToken:
        with Session(self._engine) as session:
            row = session.get(PromptAssentToken, token_id)
        if row is None:  # pragma: no cover - we just wrote it
            raise LookupError(f"assent token {token_id} vanished after write")
        return row


def _now() -> str:
    """Deferred-import clock stamp (upgrade_journal SSOT lineage)."""
    from daemon.tools.upgrade_journal import now_iso

    return now_iso()


def _row_values(row: PromptAssentToken) -> dict[str, Any]:
    """Column-value dict for a core-INSERT (bypasses SQLModel validation)."""
    return {
        "token_id": row.token_id,
        "version_id": row.version_id,
        "agent_id": row.agent_id,
        "version_tag": row.version_tag,
        "artifact": row.artifact,
        "version": row.version,
        "token_hash": row.token_hash,
        "render_sha256": row.render_sha256,
        "mint_source_id": row.mint_source_id,
        "mint_channel_id": row.mint_channel_id,
        "mint_is_dm": row.mint_is_dm,
        "mint_user_id": row.mint_user_id,
        "minted_external_user_id": row.minted_external_user_id,
        "issued_at": row.issued_at,
        "ttl_expires_at": row.ttl_expires_at,
        "superseded_at": row.superseded_at,
        "consumed_at": row.consumed_at,
        "consumed_by_message_id": row.consumed_by_message_id,
        "attempts": row.attempts,
        "status": row.status,
    }


def _not_live_code(conn: Any, token_id: str) -> tuple[str, str]:
    """Precise taxonomy code for a consume-CAS miss (row read-back)."""
    row = conn.execute(
        select(col(PromptAssentToken.status))
        .where(col(PromptAssentToken.token_id) == token_id)
    ).first()
    if row is None:
        return ("token-mismatch", f"token {token_id} not found")
    status = row[0]
    if status == STATUS_CONSUMED:
        return ("token-already-used", f"token {token_id} already consumed")
    if status == STATUS_SUPERSEDED:
        return (
            "token-superseded",
            f"token {token_id} was superseded by a re-mint",
        )
    return (
        "token-mismatch",
        f"token {token_id} is not live (status={status!r})",
    )


# Re-exported for callers that introspect the vocabulary (keep the import
# list meaningful; TOKEN_STATUSES + the table name are part of the public
# surface of this package).
__all__ = [
    "AssentTokenError",
    "PROMPT_ASSENT_TOKENS_TABLE",
    "PromptAssentTokenRepository",
    "REVOKE_THRESHOLD_DEFAULT",
    "STATUS_CONSUMED",
    "STATUS_LIVE",
    "STATUS_REVOKED",
    "STATUS_SUPERSEDED",
    "TOKEN_STATUSES",
]
