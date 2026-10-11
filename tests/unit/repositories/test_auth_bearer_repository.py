"""Bearer-token repository tests — oauth2-auth Phase 2 (G2.1 / NFR-13).

Hash-only storage: the repository API accepts ``SHA-256(token)`` hex
ONLY — this module also greps a raw row dump to prove the plaintext
never reaches the database. Revoked and expired rows are skipped by
lookup; revocation is soft (``revoked_at``) and idempotent (first
timestamp wins). Both backend legs per the repo convention.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from daemon.repositories.auth_session.models import BearerToken
from daemon.repositories.auth_session.repository import AuthSessionRepository
from tests.unit.auth_pg_scratch_helper import (  # noqa: F401
    dual_backend_engine,
)

_T0 = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
#: Fake plaintext token — must NEVER appear in any persisted row.
PLAINTEXT_TOKEN = "plaintext-secret-token-DO-NOT-PERSIST-9f2c41"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _bearer(
    id_hash: str,
    *,
    expires: datetime | None = None,
    revoked: str | None = None,
    label: str | None = "ci-token",
) -> BearerToken:
    return BearerToken(
        id_hash=id_hash,
        subject="auth:admin",
        scopes="auth:admin",
        label=label,
        created_at=_iso(_T0),
        expires_at=_iso(expires or (_T0 + timedelta(days=30))),
        last_used_at=None,
        revoked_at=revoked,
    )


class TestHashOnlyStorage:
    def test_plaintext_never_persisted(self, dual_backend_engine):
        repo = AuthSessionRepository(dual_backend_engine)
        repo.insert_bearer(_bearer(_hash(PLAINTEXT_TOKEN)))

        # Raw row dump (no ORM) — the plaintext string appears NOWHERE.
        with dual_backend_engine.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM auth_bearer_tokens")
            ).fetchall()
        assert len(rows) == 1
        dumped = repr(rows)
        assert PLAINTEXT_TOKEN not in dumped
        assert _hash(PLAINTEXT_TOKEN) in dumped


class TestGetByHash:
    def test_hit_for_active_token(self, dual_backend_engine):
        repo = AuthSessionRepository(dual_backend_engine)
        repo.insert_bearer(_bearer(_hash(PLAINTEXT_TOKEN)))
        row = repo.get_bearer_by_hash(_hash(PLAINTEXT_TOKEN), now_iso=_iso(_T0))
        assert row is not None
        assert row.subject == "auth:admin"
        assert row.label == "ci-token"

    def test_miss_unknown_hash(self, dual_backend_engine):
        repo = AuthSessionRepository(dual_backend_engine)
        assert (
            repo.get_bearer_by_hash("f" * 64, now_iso=_iso(_T0)) is None
        )

    def test_revoked_rows_skipped(self, dual_backend_engine):
        repo = AuthSessionRepository(dual_backend_engine)
        repo.insert_bearer(
            _bearer(_hash(PLAINTEXT_TOKEN), revoked=_iso(_T0 - timedelta(minutes=1)))
        )
        assert (
            repo.get_bearer_by_hash(_hash(PLAINTEXT_TOKEN), now_iso=_iso(_T0))
            is None
        )

    def test_expired_rows_skipped(self, dual_backend_engine):
        repo = AuthSessionRepository(dual_backend_engine)
        repo.insert_bearer(
            _bearer(_hash(PLAINTEXT_TOKEN), expires=_T0 - timedelta(seconds=1))
        )
        assert (
            repo.get_bearer_by_hash(_hash(PLAINTEXT_TOKEN), now_iso=_iso(_T0))
            is None
        )


class TestRevoke:
    def test_revoke_sets_revoked_at_once(self, dual_backend_engine):
        repo = AuthSessionRepository(dual_backend_engine)
        repo.insert_bearer(_bearer(_hash(PLAINTEXT_TOKEN)))
        first_ts = _iso(_T0)
        assert repo.revoke_bearer(_hash(PLAINTEXT_TOKEN), now_iso=first_ts) is True
        # Idempotent: a second revoke does NOT move the timestamp.
        later = _iso(_T0 + timedelta(minutes=5))
        assert repo.revoke_bearer(_hash(PLAINTEXT_TOKEN), now_iso=later) is False
        with dual_backend_engine.connect() as conn:
            stored = conn.execute(
                text("SELECT revoked_at FROM auth_bearer_tokens")
            ).scalar_one()
        assert stored == first_ts


class TestBearerSweep:
    def test_sweep_deletes_expired_keeps_revoked_unexpired(
        self, dual_backend_engine
    ):
        repo = AuthSessionRepository(dual_backend_engine)
        repo.insert_bearer(
            _bearer("a" * 64, expires=_T0 - timedelta(seconds=1))
        )
        # Revoked but NOT expired — retained until natural expiry (the
        # revocation audit window), then swept by expires_at.
        repo.insert_bearer(
            _bearer(
                "b" * 64,
                expires=_T0 + timedelta(days=1),
                revoked=_iso(_T0 - timedelta(minutes=1)),
            )
        )
        repo.insert_bearer(
            _bearer("c" * 64, expires=_T0 + timedelta(days=1))
        )
        deleted = repo.sweep_expired_bearer_tokens(
            cutoff_iso=_iso(_T0), batch_size=100
        )
        assert deleted == 1
        with dual_backend_engine.connect() as conn:
            remaining = {
                r[0]
                for r in conn.execute(
                    text("SELECT id_hash FROM auth_bearer_tokens")
                ).fetchall()
            }
        assert remaining == {"b" * 64, "c" * 64}
