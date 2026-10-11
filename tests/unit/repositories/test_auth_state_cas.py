"""AuthState exactly-once CAS tests — oauth2-auth Phase 2 (G2.1 / D2.3).

The CAS is a single portable conditional UPDATE
(``SET consumed_at=:now WHERE id=:id AND consumed_at IS NULL``) — the
exactly-once arbitration point. Both backend legs run per the repo
convention (scratch SQLite always; scratch PostgreSQL via the shared
helper, skip-clean when unreachable).

Outcome mapping under test: ``REPLAYED`` → P3's ``400 STATE_REPLAYED``;
``EXPIRED`` → ``400 STATE_EXPIRED``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from daemon.repositories.auth_session.models import AuthState
from daemon.repositories.auth_session.repository import (
    AuthSessionRepository,
    StateConsumeOutcome,
)
from tests.unit.auth_pg_scratch_helper import (  # noqa: F401
    dual_backend_engine,
)

_T0 = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _state(
    sid: str,
    *,
    expires: datetime | None = None,
    consumed: str | None = None,
) -> AuthState:
    return AuthState(
        id=sid,
        code_verifier="v" * 43,
        code_challenge="c" * 43,
        challenge_method="S256",
        redirect_to="/dashboard",
        created_at=_iso(_T0 - timedelta(minutes=5)),
        expires_at=_iso(expires or (_T0 + timedelta(minutes=10))),
        consumed_at=consumed,
    )


class TestCasWinOnce:
    def test_first_consume_wins(self, dual_backend_engine):
        repo = AuthSessionRepository(dual_backend_engine)
        repo.insert_state(_state("st-1"))
        outcome = repo.consume_state(
            "st-1", now=_T0, now_iso=_iso(_T0)
        )
        assert outcome is StateConsumeOutcome.SUCCESS
        row = repo.get_state("st-1")
        assert row is not None
        assert row.consumed_at == _iso(_T0)

    def test_second_consume_is_replayed(self, dual_backend_engine):
        repo = AuthSessionRepository(dual_backend_engine)
        repo.insert_state(_state("st-1"))
        assert (
            repo.consume_state("st-1", now=_T0, now_iso=_iso(_T0))
            is StateConsumeOutcome.SUCCESS
        )
        assert (
            repo.consume_state(
                "st-1", now=_T0 + timedelta(seconds=1),
                now_iso=_iso(_T0 + timedelta(seconds=1)),
            )
            is StateConsumeOutcome.REPLAYED
        )

    def test_unknown_state_not_found(self, dual_backend_engine):
        repo = AuthSessionRepository(dual_backend_engine)
        assert (
            repo.consume_state("ghost", now=_T0, now_iso=_iso(_T0))
            is StateConsumeOutcome.NOT_FOUND
        )


class TestExpiredPaths:
    def test_expired_unconsumed_wins_then_maps_expired(self, dual_backend_engine):
        """An expired-but-pending state WINS the CAS (consumed_at was
        NULL) yet maps to EXPIRED — the row stays consumed so the
        replay window is dead either way (expired dominates)."""
        repo = AuthSessionRepository(dual_backend_engine)
        repo.insert_state(
            _state("st-exp", expires=_T0 - timedelta(seconds=1))
        )
        outcome = repo.consume_state("st-exp", now=_T0, now_iso=_iso(_T0))
        assert outcome is StateConsumeOutcome.EXPIRED
        row = repo.get_state("st-exp")
        assert row is not None
        assert row.consumed_at == _iso(_T0)

    def test_consumed_expired_loser_maps_expired(self, dual_backend_engine):
        repo = AuthSessionRepository(dual_backend_engine)
        repo.insert_state(
            _state(
                "st-exp2",
                expires=_T0 - timedelta(seconds=1),
                consumed=_iso(_T0 - timedelta(seconds=2)),
            )
        )
        assert (
            repo.consume_state("st-exp2", now=_T0, now_iso=_iso(_T0))
            is StateConsumeOutcome.EXPIRED
        )

    def test_expired_priority_over_replayed(self, dual_backend_engine):
        """Expired dominates: a consumed AND expired state reports
        EXPIRED (the honest answer — the state window is over), not
        REPLAYED."""
        repo = AuthSessionRepository(dual_backend_engine)
        repo.insert_state(
            _state(
                "st-both",
                expires=_T0 - timedelta(minutes=1),
                consumed=_iso(_T0 - timedelta(minutes=2)),
            )
        )
        assert (
            repo.consume_state("st-both", now=_T0, now_iso=_iso(_T0))
            is StateConsumeOutcome.EXPIRED
        )


class TestConcurrentConsume:
    def test_two_racing_tasks_exactly_one_winner(self, dual_backend_engine):
        """Two concurrent consumers race the CAS: exactly one SUCCESS,
        exactly one REPLAYED — the rowcount IS the verdict (no
        SELECT-then-UPDATE window)."""
        repo = AuthSessionRepository(dual_backend_engine)
        repo.insert_state(_state("st-race"))

        async def race():
            return await asyncio.gather(
                asyncio.to_thread(
                    repo.consume_state, "st-race", now=_T0, now_iso=_iso(_T0)
                ),
                asyncio.to_thread(
                    repo.consume_state,
                    "st-race",
                    now=_T0,
                    now_iso=_iso(_T0),
                ),
            )

        outcomes = asyncio.run(race())
        assert sorted(o.value for o in outcomes) == sorted(
            [StateConsumeOutcome.SUCCESS.value, StateConsumeOutcome.REPLAYED.value]
        )
        # The row is consumed exactly once — a single consumed_at value.
        row = repo.get_state("st-race")
        assert row is not None
        assert row.consumed_at == _iso(_T0)
