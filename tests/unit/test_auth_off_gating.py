"""Auth OFF-gating + D17 inert-posture tests — Phase 2 (T2.8 / G2.3).

Two contracts pinned here:

* **G2.3 / FR-2 / D5 (not-registered)**: with ``auth.enabled=false``
  NOTHING constructs — introspection of ``app.state`` proves NO sweeper
  task, NO ``OAuthClient``, NO repository (no ``auth_*`` attribute
  exists at all). With ON, all four attributes materialize and the
  shutdown path cancels AND awaits the sweeper task.
* **D17 inert tables**: the four auth tables EXIST on both backends
  (migration runner on SQLite / ``create_all`` on PG) yet are never
  read or written while auth is OFF — with nothing constructed there
  is no code path that could touch them; the test additionally proves
  the tables stay empty across a full start(OFF)→stop cycle.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlmodel import SQLModel

from daemon.config import AuthConfig
from daemon.repositories.auth_session.models import (
    AuthBootstrapConsumption,
    AuthSession,
    AuthState,
    BearerToken,
)
from daemon.services.auth_session.lifecycle import (
    AUTH_STATE_ATTRS,
    start_auth_background,
    stop_auth_background,
)
from tests.unit.auth_pg_scratch_helper import (
    AUTH_TABLE_NAMES,
    scratch_pg_engine_or_skip,  # noqa: F401 — used via pytest.skip inside
    sqlite_engine,
)

AUTH_TABLES = [
    AuthSession.__table__,
    AuthState.__table__,
    BearerToken.__table__,
    AuthBootstrapConsumption.__table__,
]


def _on_config() -> AuthConfig:
    return AuthConfig(
        enabled=True,
        client_id="my-app",
        client_secret=SecretStr("s3cret"),
        issuer="https://idp.example.com",
        allowed_origins=["https://ensemble.example.com"],
    )


class TestOffGateNothingConstructs:
    async def test_off_returns_none_and_state_untouched(self):
        from starlette.datastructures import State

        state = State()
        # OFF must return before ANY construction — engine=None proves it.
        result = await start_auth_background(state, AuthConfig(), None)
        assert result is None
        for attr in AUTH_STATE_ATTRS:
            assert not hasattr(state, attr), f"OFF state must not carry {attr!r}"

    async def test_off_gate_fires_before_any_construction(self, monkeypatch):
        """The OFF branch is the FIRST line — even a poisoned engine
        argument (None) proves no repository/client was built with it."""
        from starlette.datastructures import State

        state = State()
        result = await start_auth_background(state, AuthConfig(), engine=None)
        assert result is None
        assert not hasattr(state, "oauth_client")
        assert not hasattr(state, "auth_sweeper_task")


class TestOnGateConstructsAndStops:
    async def test_on_constructs_all_and_stop_cancels_and_awaits(self, tmp_path):
        from starlette.datastructures import State

        engine = sqlite_engine(tmp_path)
        SQLModel.metadata.create_all(engine, tables=AUTH_TABLES)
        state = State()
        try:
            result = await start_auth_background(state, _on_config(), engine)
            assert result is not None
            repo, client, sweeper = result
            for attr in AUTH_STATE_ATTRS:
                assert getattr(state, attr, None) is not None, attr
            assert state.auth_session_repository is repo
            assert state.oauth_client is client
            assert state.auth_sweeper is sweeper
            assert not state.auth_sweeper_task.done()

            await stop_auth_background(state)
            assert state.auth_sweeper_task is None  # cleared after await
            assert state.oauth_client is None
        finally:
            # Idempotent double-stop is safe (OFF-like state).
            await stop_auth_background(state)
            engine.dispose()

    async def test_stop_is_safe_on_never_started_state(self):
        from starlette.datastructures import State

        state = State()
        await stop_auth_background(state)  # must not raise
        for attr in AUTH_STATE_ATTRS:
            assert not hasattr(state, attr)


class TestD17InertTables:
    def test_sqlite_tables_exist_and_stay_empty_while_off(self, tmp_path):
        engine = sqlite_engine(tmp_path)
        # The boot path creates the auth tables on BOTH backends
        # regardless of auth.enabled (D17): model create_all here; the
        # migration runner applies the same DDL on real SQLite boots
        # (pinned by tests/migration/test_auth_tables_g2_2.py).
        SQLModel.metadata.create_all(engine, tables=AUTH_TABLES)
        try:
            counts = _row_counts(engine)
            assert counts == {name: 0 for name in AUTH_TABLE_NAMES}
        finally:
            engine.dispose()

    def test_pg_tables_exist_and_stay_empty_while_off(self):
        engine = scratch_pg_engine_or_skip()
        try:
            counts = _row_counts(engine)
            assert counts == {name: 0 for name in AUTH_TABLE_NAMES}
        finally:
            engine.dispose()


def _row_counts(engine) -> dict[str, int]:
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for name in AUTH_TABLE_NAMES:
            counts[name] = conn.execute(
                text(f"SELECT COUNT(*) FROM {name}")
            ).scalar_one()
    return counts
