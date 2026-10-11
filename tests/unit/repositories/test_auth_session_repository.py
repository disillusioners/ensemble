"""AuthSessionRepository tests — oauth2-auth Phase 2 (G2.1).

Per repo convention (G2.1 note: "repository tests run on real DB") the
DB-backed legs run on BOTH backends: a file-backed scratch SQLite
(always) and the scratch PostgreSQL ``ensemble_test`` database (the
``tests/postgres`` convention coordinate, via
``tests/unit/auth_pg_scratch_helper`` — skip-clean when unreachable).

Env safety: the helper NEVER reads ``POSTGRES_*``/``DATABASE_URL`` and
carries a fail-closed tripwire against ambient live coordinates.

Covers [phase2-plan §4]: session CRUD round-trip; ``last_used_at``
batched touch (60 s min-interval gate); invalidate; sweep deletes only
``expires_at < cutoff`` rows in batches (seed 1500 → batch + remainder).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel, Session, col, select

from daemon.repositories.auth_session.models import AuthSession
from daemon.repositories.auth_session.repository import AuthSessionRepository
from tests.unit.auth_pg_scratch_helper import (
    AUTH_TABLES,
    dual_backend_engine,  # noqa: F401 — pytest fixture registration
    sqlite_engine,
)

_T0 = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _session(
    sid: str,
    *,
    created: datetime | None = None,
    expires: datetime | None = None,
    last_used: str | None = None,
) -> AuthSession:
    return AuthSession(
        id=sid,
        subject="sub-1",
        email="a@example.com",
        scopes="openid profile",
        auth_method="session",
        user_agent="pytest",
        ip_subnet=None,
        created_at=_iso(created or _T0),
        last_used_at=last_used,
        expires_at=_iso(expires or (_T0 + timedelta(hours=8))),
    )


class TestSessionRoundTrip:
    def test_insert_get(self, dual_backend_engine):
        repo = AuthSessionRepository(dual_backend_engine)
        row = _session("sess-1")
        repo.insert_session(row)
        got = repo.get_session("sess-1")
        assert got is not None
        assert got.id == "sess-1"
        assert got.subject == "sub-1"
        assert got.scopes == "openid profile"
        assert got.auth_method == "session"
        assert got.expires_at == _iso(_T0 + timedelta(hours=8))
        assert repo.get_session("missing") is None


class TestBatchedTouch:
    def test_touch_updates_and_min_interval_gates(self, dual_backend_engine):
        repo = AuthSessionRepository(dual_backend_engine)
        stale = _iso(_T0 - timedelta(hours=2))
        repo.insert_session(_session("s-a", last_used=stale))
        repo.insert_session(_session("s-b", last_used=None))

        now = _iso(_T0)
        # Both rows touched: stale row is older than the 60 s floor; the
        # NULL row has never been touched.
        assert repo.touch_sessions(["s-a", "s-b"], now_iso=now) == 2
        a = repo.get_session("s-a")
        b = repo.get_session("s-b")
        assert a.last_used_at == now
        assert b.last_used_at == now

        # Immediate re-touch is suppressed by the min-interval gate
        # (avoids a write per validate request).
        later = _iso(_T0 + timedelta(seconds=5))
        assert repo.touch_sessions(["s-a", "s-b"], now_iso=later) == 0

        # Past the 60 s floor the touch lands again.
        much_later = _iso(_T0 + timedelta(minutes=2))
        assert repo.touch_sessions(["s-a"], now_iso=much_later) == 1

    def test_touch_empty_batch_is_noop(self, dual_backend_engine):
        repo = AuthSessionRepository(dual_backend_engine)
        assert repo.touch_sessions([], now_iso=_iso(_T0)) == 0

    def test_touch_missing_ids_are_skipped(self, dual_backend_engine):
        repo = AuthSessionRepository(dual_backend_engine)
        repo.insert_session(_session("s-a", last_used=None))
        assert (
            repo.touch_sessions(["s-a", "ghost"], now_iso=_iso(_T0)) == 1
        )


class TestInvalidate:
    def test_invalidate_deletes_row(self, dual_backend_engine):
        repo = AuthSessionRepository(dual_backend_engine)
        repo.insert_session(_session("s-gone"))
        assert repo.invalidate_session("s-gone") is True
        assert repo.get_session("s-gone") is None
        # Logout of an already-dead session is a False, not an error.
        assert repo.invalidate_session("s-gone") is False


class TestSweepBatching:
    def test_sweep_deletes_expired_in_batches(self, dual_backend_engine):
        repo = AuthSessionRepository(dual_backend_engine)
        cutoff = _iso(_T0)
        expired_edge = _iso(_T0 - timedelta(seconds=1))
        live_edge = _iso(_T0 + timedelta(seconds=1))
        # 1500 expired + 500 live: sweep with batch_size=1000 must
        # delete exactly the 1500 (full batch + remainder) and leave
        # every live row untouched.
        with Session(dual_backend_engine) as session:
            for i in range(1500):
                session.add(
                    _session(f"exp-{i}", expires=datetime.fromisoformat(expired_edge))
                )
            for i in range(500):
                session.add(
                    _session(f"live-{i}", expires=datetime.fromisoformat(live_edge))
                )
            session.commit()

        deleted = repo.sweep_expired_sessions(cutoff_iso=cutoff, batch_size=1000)
        assert deleted == 1500  # 1000 (full batch) + 500 (remainder)

        with Session(dual_backend_engine) as session:
            remaining_expired = session.exec(
                select(AuthSession).where(col(AuthSession.id).startswith("exp-"))
            ).all()
            remaining_live = session.exec(
                select(AuthSession).where(col(AuthSession.id).startswith("live-"))
            ).all()
        assert remaining_expired == []
        assert len(remaining_live) == 500

    def test_sweep_boundary_is_strictly_older(self, dual_backend_engine):
        repo = AuthSessionRepository(dual_backend_engine)
        cutoff = _iso(_T0)
        repo.insert_session(_session("at-cutoff", expires=_T0))
        repo.insert_session(
            _session("before-cutoff", expires=_T0 - timedelta(seconds=1))
        )
        deleted = repo.sweep_expired_sessions(cutoff_iso=cutoff, batch_size=100)
        assert deleted == 1
        assert repo.get_session("at-cutoff") is not None
        assert repo.get_session("before-cutoff") is None


class TestTablesMaterializeOnBothBackends:
    def test_auth_tables_exist(self, dual_backend_engine):
        engine = dual_backend_engine
        if engine.url.get_backend_name() == "postgresql":
            with engine.connect() as conn:
                for table in ("auth_sessions", "auth_states", "auth_bearer_tokens", "auth_bootstrap_consumption"):
                    assert conn.execute(
                        text("SELECT to_regclass(:r)", ), {"r": f"public.{table}"}
                    ).scalar() is not None
        else:
            with engine.connect() as conn:
                names = {
                    row[0]
                    for row in conn.execute(
                        text("SELECT name FROM sqlite_master WHERE type='table'")
                    ).fetchall()
                }
                assert {"auth_sessions", "auth_states", "auth_bearer_tokens",
                        "auth_bootstrap_consumption"} <= names


def test_sqlite_leg_tables(tmp_path):
    """The SQLite leg runs even when scratch PG is unreachable."""
    engine = sqlite_engine(tmp_path)
    SQLModel.metadata.create_all(engine, tables=AUTH_TABLES)
    repo = AuthSessionRepository(engine)
    repo.insert_session(_session("lite-1"))
    assert repo.get_session("lite-1") is not None
    engine.dispose()
