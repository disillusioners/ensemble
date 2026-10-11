"""AuthSessionSweeper tests — oauth2-auth Phase 2 (T2.4 / G2.1 / F2).

Covers [phase2-plan §4]: sweeper interval config; the state sweeper
deletes consumed+expired; no interference with live rows. Plus:

* **AMEND v1.3 / F2 dual-dialect shapes** — the batched sweep's
  ``with_for_update(skip_locked=True)`` renders ``FOR UPDATE SKIP
  LOCKED`` on the PostgreSQL dialect and silently no-ops to a plain
  batched SELECT+DELETE on SQLite; BOTH shapes are asserted (compile-
  level on both dialects + functional runs on the real backends) and
  the repository source carries NO runtime dialect branch (D17 single
  code path).
* **Per-batch deadline** — a wedged backend costs one deadline per
  tick, then the loop continues (portable wait_for spelling).
* **Boot hook** — runs once, best-effort (exceptions swallowed).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy.dialects.postgresql as pg_dialect
import sqlalchemy.dialects.sqlite as sqlite_dialect
from sqlmodel import Session, select

from daemon.repositories.auth_session import models as auth_models
from daemon.repositories.auth_session.models import AuthSession, AuthState, BearerToken
from daemon.repositories.auth_session.repository import AuthSessionRepository
from daemon.services.auth_session.sweeper import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_SESSION_INTERVAL_SECONDS,
    DEFAULT_STATE_INTERVAL_SECONDS,
    AuthSessionSweeper,
)
from tests.unit.auth_pg_scratch_helper import (  # noqa: F401
    dual_backend_engine,
)

_T0 = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ── F2: dual-dialect shape assertions (compile-level) ───────────────


class TestF2DialectShapes:
    def test_pg_shape_renders_skip_locked(self):
        stmt = (
            select(AuthSession.id)
            .limit(1000)
            .with_for_update(skip_locked=True)
        )
        pg_sql = str(stmt.compile(dialect=pg_dialect.dialect()))
        assert "FOR UPDATE SKIP LOCKED" in pg_sql

    def test_sqlite_shape_is_plain_batched_select(self):
        stmt = (
            select(AuthSession.id)
            .limit(1000)
            .with_for_update(skip_locked=True)
        )
        lite_sql = str(stmt.compile(dialect=sqlite_dialect.dialect()))
        assert "FOR UPDATE" not in lite_sql
        assert "SKIP LOCKED" not in lite_sql
        # The identical statement object — no alternate query built.
        assert stmt._for_update_arg is not None  # noqa: SLF001 — same object both dialects

    def test_no_runtime_dialect_branch_in_repository(self):
        """D17 single code path: the repository module must contain the
        ONE dialect-agnostic spelling and no dialect-conditional
        branching (no ``dialect ==``, ``is_postgres``, ``sqlite``/
        ``postgresql`` string dispatch in code)."""
        source = Path(
            auth_models.__file__  # anchor: daemon/repositories/auth_session/
        ).parent.joinpath("repository.py").read_text()
        assert "with_for_update(skip_locked=True)" in source
        for forbidden in (
            "dialect ==",
            "dialect.name ==",
            "is_postgres",
            "if postgres",
            "if sqlite",
            '== "postgresql"',
            '== "sqlite"',
        ):
            assert forbidden not in source, f"runtime dialect branch found: {forbidden!r}"


# ── interval / config surface ───────────────────────────────────────


class TestSweeperConfig:
    def test_defaults_match_plan(self, dual_backend_engine):
        sweeper = AuthSessionSweeper(AuthSessionRepository(dual_backend_engine))
        assert sweeper._session_interval == DEFAULT_SESSION_INTERVAL_SECONDS == 300
        assert sweeper._state_interval == DEFAULT_STATE_INTERVAL_SECONDS == 60
        assert sweeper._batch_size == DEFAULT_BATCH_SIZE == 1000

    def test_intervals_are_configurable(self, dual_backend_engine):
        sweeper = AuthSessionSweeper(
            AuthSessionRepository(dual_backend_engine),
            session_interval_seconds=11,
            state_interval_seconds=2,
            batch_size=7,
        )
        assert sweeper._session_interval == 11
        assert sweeper._state_interval == 2
        assert sweeper._batch_size == 7


# ── functional sweeps on BOTH backends ──────────────────────────────


class TestStateSweep:
    def test_state_sweep_deletes_consumed_and_expired_keeps_live(
        self, dual_backend_engine
    ):
        repo = AuthSessionRepository(dual_backend_engine)
        cutoff = _iso(_T0)
        # consumed (any age) → deleted
        repo.insert_state(
            AuthState(
                id="st-consumed",
                code_verifier="v" * 43,
                code_challenge="c" * 43,
                challenge_method="S256",
                redirect_to=None,
                created_at=_iso(_T0 - timedelta(minutes=5)),
                expires_at=_iso(_T0 + timedelta(minutes=10)),
                consumed_at=_iso(_T0 - timedelta(seconds=30)),
            )
        )
        # expired, unconsumed → deleted
        repo.insert_state(
            AuthState(
                id="st-expired",
                code_verifier="v" * 43,
                code_challenge="c" * 43,
                challenge_method="S256",
                redirect_to=None,
                created_at=_iso(_T0 - timedelta(minutes=5)),
                expires_at=_iso(_T0 - timedelta(seconds=1)),
                consumed_at=None,
            )
        )
        # live (pending, unexpired) → KEPT
        repo.insert_state(
            AuthState(
                id="st-live",
                code_verifier="v" * 43,
                code_challenge="c" * 43,
                challenge_method="S256",
                redirect_to=None,
                created_at=_iso(_T0),
                expires_at=_iso(_T0 + timedelta(minutes=5)),
                consumed_at=None,
            )
        )
        deleted = repo.sweep_expired_states(cutoff_iso=cutoff)
        assert deleted == 2
        assert repo.get_state("st-live") is not None
        assert repo.get_state("st-consumed") is None
        assert repo.get_state("st-expired") is None


class TestSessionTokenSweepTick:
    def test_tick_deletes_only_expired(self, dual_backend_engine):
        repo = AuthSessionRepository(dual_backend_engine)
        cutoff = _iso(_T0)
        with Session(dual_backend_engine) as session:
            session.add(
                AuthSession(
                    id="s-exp", subject="x", scopes="openid",
                    auth_method="session", created_at=cutoff,
                    expires_at=_iso(_T0 - timedelta(seconds=1)),
                )
            )
            session.add(
                AuthSession(
                    id="s-live", subject="x", scopes="openid",
                    auth_method="session", created_at=cutoff,
                    expires_at=_iso(_T0 + timedelta(hours=1)),
                )
            )
            session.add(
                BearerToken(
                    id_hash="d" * 64, subject="x", scopes="auth:admin",
                    created_at=cutoff,
                    expires_at=_iso(_T0 - timedelta(seconds=1)),
                )
            )
            session.commit()

        sweeper = AuthSessionSweeper(repo, now_iso_fn=lambda: _iso(_T0))
        asyncio.run(sweeper._sweep_sessions_tokens_once())

        assert repo.get_session("s-exp") is None
        assert repo.get_session("s-live") is not None
        with Session(dual_backend_engine) as session:
            remaining = {
                t.id_hash for t in session.exec(select(BearerToken)).all()
            }
        assert remaining == set()  # expired bearer deleted


# ── boot hook + per-batch deadline ──────────────────────────────────


class TestBootHook:
    def test_boot_hook_runs_once_and_failure_is_nonfatal(
        self, dual_backend_engine
    ):
        calls: list[int] = []

        async def hook() -> object:
            calls.append(1)
            raise RuntimeError("idp down — must NOT kill the sweeper")

        async def scenario() -> None:
            sweeper = AuthSessionSweeper(
                AuthSessionRepository(dual_backend_engine), boot_hook=hook
            )
            task = asyncio.create_task(sweeper.run())
            # The boot hook fired (exception swallowed) and the loops
            # are alive past it.
            await _sleep_until(lambda: len(calls) >= 1, timeout=2.0)
            assert not task.done()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(scenario())
        assert calls == [1]  # ran exactly once, exception swallowed


async def _sleep_until(predicate, *, timeout: float) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met before timeout")
        await asyncio.sleep(0.01)


class TestPerBatchDeadline:
    def test_wedged_backend_skips_tick_and_loop_continues(
        self, dual_backend_engine, caplog
    ):
        class WedgedRepo(AuthSessionRepository):
            def sweep_expired_sessions(self, *, cutoff_iso, batch_size=1000):
                import time

                time.sleep(0.5)  # wedged backend
                return 0

            def sweep_expired_bearer_tokens(self, *, cutoff_iso, batch_size=1000):
                return 0

        sweeper = AuthSessionSweeper(
            WedgedRepo(dual_backend_engine),
            batch_timeout_seconds=0.05,
        )
        with caplog.at_level("WARNING", logger="daemon.services.auth_session.sweeper"):
            # The deadline converts the wedged batch into a skipped tick
            # (0), NOT a crash.
            deleted = asyncio.run(sweeper._sweep_sessions_tokens_once())
        assert deleted == 0
        assert any("per-batch deadline" in r.message for r in caplog.records)
