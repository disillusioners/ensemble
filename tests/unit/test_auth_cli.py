"""Mint-CLI tests — oauth2-auth Phase 4 (T4.1 / D4.1, plan §4 row 1).

Asserts: mint prints plaintext exactly once; the DB row stores sha256
hex + label + scopes + expiry (AC-16); ``--bootstrap`` without env →
the helpful failure (§6.3 row 10); revoke flips ``revoked_at``; a
revoked token is rejected at lookup; the ONE-SHOT consumption sentinel
(AMENDED v1.1 / C2 (b), AMEND v1.3 / F10): the second
``issue-token --bootstrap`` with the same env value is REJECTED with
the consumed_at/actor_label detail and the sentinel row is created on
the first mint and UNCHANGED by the rejected second mint.
"""

from __future__ import annotations

import hashlib

import pytest
from sqlmodel import Session

from daemon.auth.cli import (
    BOOTSTRAP_ENV_VAR,
    main,
)
from daemon.auth.scopes import SESSION_OPERATOR_SCOPES
from daemon.repositories.auth_session.models import (
    AuthBootstrapConsumption,
    AuthSession,
    AuthState,
    BearerToken,
)
from daemon.repositories.auth_session.repository import AuthSessionRepository
from sqlmodel import SQLModel
from tests.helpers.auth_fixture import on_auth_config

AUTH_TABLES = [
    AuthSession.__table__,
    AuthState.__table__,
    BearerToken.__table__,
    AuthBootstrapConsumption.__table__,
]


@pytest.fixture()
def scratch(tmp_path):
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{tmp_path}/cli_test.db", future=True)
    SQLModel.metadata.create_all(engine, tables=AUTH_TABLES)
    return engine


class _StrStream:
    def __init__(self) -> None:
        self.chunks: list[str] = []

    def write(self, text: str) -> None:
        self.chunks.append(text)

    def flush(self) -> None:  # pragma: no cover — stream protocol
        pass

    @property
    def value(self) -> str:
        return "".join(self.chunks)


def _run(argv, engine, env=None):
    out, err = _StrStream(), _StrStream()
    code = main(argv, engine=engine, env=env or {}, stdout=out, stderr=err)
    return code, out.value, err.value


class TestMintShape:
    def test_mint_prints_plaintext_exactly_once(self, scratch):
        code, out, err = _run(
            ["issue-token", "--label", "operator-cli", "--scopes", "auth:admin"],
            scratch,
        )
        assert code == 0
        lines = [l for l in out.strip().splitlines() if l]
        # EXACTLY ONE stdout line — the plaintext, nothing else.
        assert len(lines) == 1
        token = lines[0]
        assert len(token) >= 32
        # The status line (scopes/ttl) rides stderr, never stdout.
        assert "issued token" in err
        assert token not in err

    def test_row_stores_sha256_only(self, scratch):
        code, out, _ = _run(
            ["issue-token", "--label", "mcpservers-test", "--scopes", "mcp:read,system:read"],
            scratch,
        )
        assert code == 0
        token = out.strip()
        expected_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        repo = AuthSessionRepository(scratch)
        rows = repo.get_bearer_hashes_by_label("mcpservers-test")
        assert len(rows) == 1
        row = rows[0]
        assert row.id_hash == expected_hash  # AC-16: sha256 hex PK
        assert row.label == "mcpservers-test"
        assert row.scopes == "mcp:read,system:read"
        assert row.subject == "mcpservers-test"
        assert row.revoked_at is None
        assert row.expires_at > row.created_at
        # The plaintext NEVER persisted (NFR-13): no column carries it.
        dump = repr(rows[0])
        assert token not in dump

    def test_ttl_default_from_config(self, scratch):
        config = on_auth_config(bearer_token_ttl_seconds=3600)
        code, out, err = _run(
            ["issue-token", "--label", "l1", "--scopes", "auth:admin"],
            scratch,
            env={},
        )
        assert code == 0
        # Config-driven default via the config kwarg path:
        from daemon.auth.cli import main as _main

        out2, err2 = _StrStream(), _StrStream()
        code2 = _main(
            ["issue-token", "--label", "l2", "--scopes", "auth:admin"],
            engine=scratch,
            config=config,
            env={},
            stdout=out2,
            stderr=err2,
        )
        assert code2 == 0
        assert "ttl_seconds=3600" in err2.value

    def test_unknown_scope_rejected(self, scratch):
        code, out, err = _run(
            ["issue-token", "--label", "l", "--scopes", "galaxy:destroy"],
            scratch,
        )
        assert code == 1
        assert "unknown scope" in err
        assert out == ""  # nothing minted, nothing printed

    def test_non_bootstrap_requires_scopes(self, scratch):
        code, _, err = _run(["issue-token", "--label", "l"], scratch)
        assert code == 1
        assert "--scopes is required" in err


class TestBootstrapGate:
    def test_bootstrap_without_env_helpful_failure(self, scratch):
        code, out, err = _run(
            ["issue-token", "--label", "first-admin", "--bootstrap"],
            scratch,
            env={},
        )
        assert code == 1
        # §6.3 row 10: the helpful message, not a traceback.
        assert (
            "set ENSEMBLE_AUTH_BOOTSTRAP_TOKEN to mint first admin token"
            in err
        )
        assert out == ""

    def test_bootstrap_first_mint_defaults_auth_admin_only(self, scratch):
        """OD-14: the first-mint default scopes are auth:admin ONLY."""
        code, out, err = _run(
            ["issue-token", "--label", "first-admin", "--bootstrap"],
            scratch,
            env={BOOTSTRAP_ENV_VAR: "boot-material-1"},
        )
        assert code == 0
        assert "scopes=auth:admin" in err
        repo = AuthSessionRepository(scratch)
        (row,) = repo.get_bearer_hashes_by_label("first-admin")
        assert row.scopes == "auth:admin"
        # The operator family is NOT implied on bearer mints.
        assert not (set(row.scopes.split(",")) & (SESSION_OPERATOR_SCOPES - {"auth:admin"}))

    def test_bootstrap_warns_on_use(self, scratch):
        code, _, err = _run(
            ["issue-token", "--label", "first-admin", "--bootstrap"],
            scratch,
            env={BOOTSTRAP_ENV_VAR: "boot-material-2"},
        )
        assert code == 0
        assert "shell history" in err


class TestOneShotConsumption:
    def _sentinel(self, scratch, env_value):
        sha = hashlib.sha256(env_value.encode("utf-8")).hexdigest()
        with Session(scratch) as s:
            return s.get(AuthBootstrapConsumption, sha)

    def test_first_mint_creates_sentinel_row(self, scratch):
        env_value = "one-shot-material"
        code, out, err = _run(
            ["issue-token", "--label", "first-admin", "--bootstrap"],
            scratch,
            env={BOOTSTRAP_ENV_VAR: env_value},
        )
        assert code == 0
        sentinel = self._sentinel(scratch, env_value)
        assert sentinel is not None
        assert sentinel.actor_label == "first-admin"
        assert sentinel.consumed_by_pid is not None  # operator debugging
        assert sentinel.consumed_at
        # Hash-only sentinel: the env value never persisted.
        assert env_value not in repr(sentinel)

    def test_second_mint_rejected_sentinel_unchanged(self, scratch):
        """F10: mint → sentinel row exists; second mint with the SAME
        env value → REJECT with the audit detail + the sentinel row
        unchanged."""
        env_value = "reuse-me-once"
        code1, out1, _ = _run(
            ["issue-token", "--label", "first-admin", "--bootstrap"],
            scratch,
            env={BOOTSTRAP_ENV_VAR: env_value},
        )
        assert code1 == 0
        before = self._sentinel(scratch, env_value)
        token1 = out1.strip()

        code2, out2, err2 = _run(
            ["issue-token", "--label", "second-admin", "--bootstrap"],
            scratch,
            env={BOOTSTRAP_ENV_VAR: env_value},
        )
        assert code2 == 1
        assert out2 == ""  # nothing minted
        assert f"already consumed at {before.consumed_at}" in err2
        assert "for first-admin" in err2
        assert "rotate the env var" in err2
        # The FIRST token remains untouched and valid at lookup.
        after = self._sentinel(scratch, env_value)
        assert (after.consumed_at, after.actor_label, after.consumed_by_pid) == (
            before.consumed_at,
            before.actor_label,
            before.consumed_by_pid,
        )
        service_repo = AuthSessionRepository(scratch)
        rows = service_repo.get_bearer_hashes_by_label("second-admin")
        assert rows == []
        # The first token still verifies (lookup by hash).
        from daemon.services.auth_service import AuthService
        from daemon.services.oauth_client import OAuthClient
        from tests.helpers.fake_oidc_stub import FakeOidcStub

        service = AuthService(
            config=on_auth_config(),
            repository=service_repo,
            oauth_client=OAuthClient(on_auth_config(), transport=FakeOidcStub().transport),
            bind_host=None,
            bind_port=None,
            host_recorder=None,
        )
        import asyncio

        ctx = asyncio.run(service.lookup_bearer(token1))
        assert ctx is not None and ctx.scopes == {"auth:admin"}

    def test_rotated_env_value_mints_again(self, scratch):
        """One-shot is PER VALUE: rotating the env var re-arms the
        bootstrap lane (the documented runbook rotation path)."""
        for i, (label, value) in enumerate(
            [("admin-a", "value-a"), ("admin-b", "value-b")]
        ):
            code, _, err = _run(
                ["issue-token", "--label", label, "--bootstrap"],
                scratch,
                env={BOOTSTRAP_ENV_VAR: value},
            )
            assert code == 0, err


class TestRevoke:
    def test_revoke_flips_revoked_at_and_lookup_rejects(self, scratch):
        code, out, _ = _run(
            ["issue-token", "--label", "rotate-me", "--scopes", "jobs:read"],
            scratch,
        )
        assert code == 0
        token = out.strip()
        repo = AuthSessionRepository(scratch)
        (row,) = repo.get_bearer_hashes_by_label("rotate-me")
        assert row.revoked_at is None

        code2, out2, err2 = _run(["revoke", "--label", "rotate-me"], scratch)
        assert code2 == 0
        assert "revoked 1 token(s)" in out2
        (after,) = repo.get_bearer_hashes_by_label("rotate-me")
        assert after.revoked_at is not None

        # The revoked token is rejected at the runtime lookup.
        from daemon.services.auth_service import AuthService
        from daemon.services.oauth_client import OAuthClient
        from tests.helpers.fake_oidc_stub import FakeOidcStub

        service = AuthService(
            config=on_auth_config(),
            repository=repo,
            oauth_client=OAuthClient(on_auth_config(), transport=FakeOidcStub().transport),
            bind_host=None,
            bind_port=None,
            host_recorder=None,
        )
        import asyncio

        assert asyncio.run(service.lookup_bearer(token)) is None

    def test_revoke_idempotent_and_unknown_label_fails(self, scratch):
        code, out, _ = _run(
            ["issue-token", "--label", "r2", "--scopes", "jobs:read"],
            scratch,
        )
        assert code == 0
        assert _run(["revoke", "--label", "r2"], scratch)[0] == 0
        code3, out3, _ = _run(["revoke", "--label", "r2"], scratch)
        assert code3 == 0  # second revoke: already-revoked rows keep state
        assert "revoked 0 token(s)" in out3
        code4, _, err4 = _run(["revoke", "--label", "never-minted"], scratch)
        assert code4 == 1
        assert "no bearer tokens carry label" in err4


class TestTablesAbsent:
    def test_unbooted_store_fails_loudly(self, tmp_path):
        from sqlalchemy import create_engine

        bare = create_engine(f"sqlite:///{tmp_path}/bare.db", future=True)
        code, out, err = _run(
            ["issue-token", "--label", "l", "--scopes", "auth:admin"],
            bare,
        )
        assert code == 1
        assert "auth tables not found" in err
        assert "boot the daemon" in err
