"""AuthService session-lane tests — oauth2-auth Phase 3 (T3.3).

Asserts the plan §4 row: session row fully populated (AC-10), TTL
default 8 h, sliding OFF by default, ip_subnet bound only when
configured, invalidate = delete + later validate miss (AC-14), the
FR-30 PG-only validate contract, and the bearer-lane delegate.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import SecretStr
from sqlmodel import Session

from daemon.config import AuthConfig
from daemon.repositories.auth_session.models import (
    BearerToken,
)
from daemon.repositories.auth_session.repository import AuthSessionRepository
from daemon.services.auth_service import AuthService
from daemon.services.oauth_client import OAuthClient
from sqlmodel import SQLModel
from tests.helpers.auth_fixture import AUTH_TABLES, on_auth_config
from tests.helpers.fake_oidc_stub import FakeOidcStub, ISSUER


def _service(tmp_path, **overrides) -> tuple[AuthService, AuthSessionRepository, object]:
    engine = _sqlite_engine(tmp_path)
    SQLModel.metadata.create_all(engine, tables=AUTH_TABLES)
    repository = AuthSessionRepository(engine)
    config = on_auth_config(**overrides)
    client = OAuthClient(config, transport=FakeOidcStub().transport)
    service = AuthService(
        config=config,
        repository=repository,
        oauth_client=client,
        bind_host="testserver",
        bind_port=80,
        host_recorder=None,
    )
    return service, repository, engine


def _sqlite_engine(tmp_path):
    from sqlalchemy import create_engine

    return create_engine(f"sqlite:///{tmp_path}/auth_session_test.db", future=True)


class TestIssueAndValidate:
    def test_issue_fully_populated_row_default_ttl(self, tmp_path):
        """AC-10 + TTL default 8h + sliding OFF default."""
        service, repository, engine = _service(tmp_path)
        now = datetime.now(timezone.utc)
        row = asyncio.run(
            service.issue_session(subject="u1", email="u1@example.com")
        )
        cfg = service._config
        assert row.id and row.subject == "u1"
        assert row.auth_method == "session"
        # P4 real-grant model (M-3 close): the session persists the
        # OIDC scopes UNION the operator family (daemon/auth/scopes.py)
        # — the P3 posture ("openid profile email" only, blanket
        # session-satisfies-all in the decorator) is superseded.
        from daemon.auth.scopes import SESSION_OPERATOR_SCOPES

        assert row.scopes == " ".join(
            sorted(set(cfg.scopes) | set(SESSION_OPERATOR_SCOPES))
        )
        assert "openid" in row.scopes and "auth:admin" in row.scopes
        assert row.created_at and row.last_used_at
        created = datetime.fromisoformat(row.created_at)
        expires = datetime.fromisoformat(row.expires_at)
        assert abs((expires - created).total_seconds() - 28800) < 2
        # fingerprint OFF → no subnet bound
        assert row.ip_subnet is None
        assert isinstance(service._config.sliding_renewal, bool)
        assert service._config.sliding_renewal is False
        _ = now

    def test_validate_roundtrip_and_context_shape(self, tmp_path):
        service, repository, engine = _service(tmp_path)
        row = asyncio.run(service.issue_session(subject="u2"))
        ctx = asyncio.run(service.validate_session(row.id))
        assert ctx is not None
        assert ctx.subject == "u2"
        assert ctx.auth_method == "session"
        assert "openid" in ctx.scopes
        assert ctx.expires_at == row.expires_at

    def test_validate_miss_on_unknown_and_expired(self, tmp_path):
        service, repository, engine = _service(tmp_path)
        assert asyncio.run(service.validate_session("nope")) is None
        row = asyncio.run(service.issue_session(subject="u3"))
        # Force expiry directly in the store.
        with Session(repository._engine) as session:
            stored = session.get(type(row), row.id)
            assert stored is not None
            stored.expires_at = (
                datetime.now(timezone.utc) - timedelta(seconds=5)
            ).isoformat()
            session.add(stored)
            session.commit()
        assert asyncio.run(service.validate_session(row.id)) is None

    def test_invalidate_is_delete_and_later_miss(self, tmp_path):
        """AC-14."""
        service, repository, engine = _service(tmp_path)
        row = asyncio.run(service.issue_session(subject="u4"))
        assert asyncio.run(service.invalidate_session(row.id)) is True
        assert asyncio.run(service.validate_session(row.id)) is None
        # Idempotent-ish: second delete finds nothing.
        assert asyncio.run(service.invalidate_session(row.id)) is False

    def test_sliding_renewal_extends_expiry(self, tmp_path):
        service, repository, engine = _service(
            tmp_path, sliding_renewal=True, session_ttl_seconds=3600
        )
        # Injectable clock: issue at T0, validate at T0+300 — the
        # renewal must push expiry to T0+300+3600 (> the issued
        # T0+3600).
        clock = {"now": datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)}

        def _now_iso() -> str:
            return clock["now"].isoformat()

        service._now_iso_fn = _now_iso
        row = asyncio.run(service.issue_session(subject="u5"))
        issued_expiry = datetime.fromisoformat(row.expires_at)
        # Backdate last_used_at WELL past the 60 s min-interval gate
        # relative to the FUTURE validate instant (T0+300): the gate is
        # last_used_at >= now-60 ⇒ anything ≤ T0+240 passes.
        with Session(repository._engine) as session:
            stored = session.get(type(row), row.id)
            assert stored is not None
            stored.last_used_at = clock["now"].isoformat()
            session.add(stored)
            session.commit()
        clock["now"] = clock["now"] + timedelta(seconds=600)
        ctx = asyncio.run(service.validate_session(row.id))
        assert ctx is not None
        new_expiry = datetime.fromisoformat(ctx.expires_at)
        assert new_expiry > issued_expiry
        assert (
            new_expiry - issued_expiry
        ).total_seconds() == 600  # exactly the elapsed window

    def test_touch_without_sliding_keeps_expiry(self, tmp_path):
        service, repository, engine = _service(tmp_path)
        row = asyncio.run(service.issue_session(subject="u6"))
        with Session(repository._engine) as session:
            stored = session.get(type(row), row.id)
            assert stored is not None
            stored.last_used_at = (
                datetime.now(timezone.utc) - timedelta(minutes=5)
            ).isoformat()
            session.add(stored)
            session.commit()
        ctx = asyncio.run(service.validate_session(row.id))
        assert ctx is not None
        assert ctx.expires_at == row.expires_at  # untouched expiry

    def test_ip_subnet_fingerprint_when_configured(self, tmp_path):
        service, repository, engine = _service(
            tmp_path, fingerprint_by_ip_subnet=True
        )
        row = asyncio.run(
            service.issue_session(subject="u7", client_ip="203.0.113.7")
        )
        assert row.ip_subnet is not None
        assert row.ip_subnet.endswith("/24")
        # Same /24 validates.
        ctx = asyncio.run(
            service.validate_session(row.id, client_ip="203.0.113.99")
        )
        assert ctx is not None
        # Different /24 → invalidates + miss.
        ctx2 = asyncio.run(
            service.validate_session(row.id, client_ip="198.51.100.1")
        )
        assert ctx2 is None
        assert asyncio.run(service.validate_session(row.id)) is None

    def test_no_subnet_binding_when_unconfigured(self, tmp_path):
        service, repository, engine = _service(tmp_path)
        row = asyncio.run(
            service.issue_session(subject="u8", client_ip="203.0.113.7")
        )
        assert row.ip_subnet is None
        ctx = asyncio.run(
            service.validate_session(row.id, client_ip="198.51.100.1")
        )
        assert ctx is not None  # IP changes are irrelevant when OFF


class TestIpSubnetNormalization:
    """Minor #7 (2026-10-11 review): IPv4-mapped IPv6 addresses
    normalize to their MAPPED IPv4 address and take the /24 branch —
    the pre-fix fall-through put them in the /64 branch, letting the
    same client defeat the fingerprint by switching address families."""

    def test_mapped_v4_takes_the_ipv4_24_branch(self):
        from daemon.services.auth_service import _ip_subnet

        assert _ip_subnet("::ffff:203.0.113.7") == "203.0.113.0/24"
        assert _ip_subnet("::ffff:198.51.100.9") == "198.51.100.0/24"

    def test_pure_ipv6_stays_64(self):
        from daemon.services.auth_service import _ip_subnet

        assert _ip_subnet("2001:db8::1").endswith("/64")
        assert _ip_subnet("2001:db8::1") == "2001:db8::/64"

    def test_mapped_v4_fingerprint_matches_its_ipv4(self, tmp_path):
        """End-to-end: a session issued from the mapped-v6 spelling of
        an IPv4 client validates from the plain IPv4 spelling (same
        /24) and refuses a foreign /24 — one identity, one family."""
        service, repository, engine = _service(
            tmp_path, fingerprint_by_ip_subnet=True
        )
        row = asyncio.run(
            service.issue_session(subject="u9", client_ip="::ffff:203.0.113.7")
        )
        assert row.ip_subnet == "203.0.113.0/24"
        ctx = asyncio.run(
            service.validate_session(row.id, client_ip="203.0.113.42")
        )
        assert ctx is not None
        ctx2 = asyncio.run(
            service.validate_session(row.id, client_ip="198.51.100.1")
        )
        assert ctx2 is None


class TestBearerDelegate:
    def test_verify_static_token_roundtrip(self, tmp_path):
        service, repository, engine = _service(tmp_path)
        token = "ens_bearer_plain_1"
        now = datetime.now(timezone.utc)
        with Session(repository._engine) as session:
            session.add(
                BearerToken(
                    id_hash=hashlib.sha256(token.encode()).hexdigest(),
                    subject="operator-cli",
                    scopes="jobs:read,mcpkb:admin",
                    label="t",
                    created_at=now.isoformat(),
                    expires_at=(now + timedelta(days=1)).isoformat(),
                )
            )
            session.commit()
        ctx = asyncio.run(service.verify_static_token(token))
        assert ctx is not None
        assert ctx.subject == "operator-cli"
        assert ctx.auth_method == "bearer"
        assert "jobs:read" in ctx.scopes

    def test_verify_rejects_unknown_revoked_expired(self, tmp_path):
        service, repository, engine = _service(tmp_path)
        now = datetime.now(timezone.utc)

        def _insert(token: str, *, expired: bool = False, revoked: bool = False) -> None:
            with Session(repository._engine) as session:
                session.add(
                    BearerToken(
                        id_hash=hashlib.sha256(token.encode()).hexdigest(),
                        subject="s",
                        scopes="",
                        label="t",
                        created_at=now.isoformat(),
                        expires_at=(
                            now - timedelta(days=1) if expired else now + timedelta(days=1)
                        ).isoformat(),
                        revoked_at=now.isoformat() if revoked else None,
                    )
                )
                session.commit()

        _insert("tok-expired", expired=True)
        _insert("tok-revoked", revoked=True)
        assert asyncio.run(service.verify_static_token("tok-unknown")) is None
        assert asyncio.run(service.verify_static_token("tok-expired")) is None
        assert asyncio.run(service.verify_static_token("tok-revoked")) is None


class TestReadinessState:
    def test_ok_by_default_and_degraded_on_idp_failure(self, tmp_path):
        """FR-30 enum: ok default; degraded when the last discovery
        attempt failed; existing sessions still validate PG-only."""
        service, repository, engine = _service(tmp_path)
        assert service.get_readiness_state() == "ok"
        service._oauth_client._last_discovery_attempted = True
        service._oauth_client._last_discovery_reachable = False
        assert service.get_readiness_state() == "degraded"
        # PG-only validate unaffected by IdP state (AC-30):
        row = asyncio.run(service.issue_session(subject="u9"))
        assert asyncio.run(service.validate_session(row.id)) is not None

    def test_unconfigured_when_redirect_chain_dead(self, tmp_path):
        service, repository, engine = _service(
            tmp_path, external_redirect_base=None
        )
        service._bind_host = None
        service._bind_port = None
        assert service.get_readiness_state() == "unconfigured"
