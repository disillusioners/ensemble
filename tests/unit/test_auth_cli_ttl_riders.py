"""ADV-4 rider tests (2026-10-11 review) — ``--ttl`` CLI validation.

The P4 file (tests/unit/test_auth_cli.py) predates TTL validation and
is READ-ONLY for this rider — these legs live in their own file by
design. Asserts: the accepted range is 60..31536000 seconds (aligned
with the ``auth.bearer_token_ttl_seconds`` §6.1 config ceiling);
``--ttl`` ≤0 and >31536000 are REFUSED with a clear stderr line and a
non-zero exit, and no token row is minted (the pre-rider defect: a
negative TTL minted an already-expired token with exit 0 —
``ttl or bearer_ttl_default`` treated the negative as truthy).
"""

from __future__ import annotations

import pytest
from sqlmodel import SQLModel

from daemon.auth.cli import main
from daemon.repositories.auth_session.models import (
    AuthBootstrapConsumption,
    AuthSession,
    AuthState,
    BearerToken,
)
from daemon.repositories.auth_session.repository import AuthSessionRepository

AUTH_TABLES = [
    AuthSession.__table__,
    AuthState.__table__,
    BearerToken.__table__,
    AuthBootstrapConsumption.__table__,
]


@pytest.fixture()
def scratch(tmp_path):
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{tmp_path}/cli_ttl_riders.db", future=True)
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


def _run(argv, engine):
    out, err = _StrStream(), _StrStream()
    code = main(argv, engine=engine, env={}, stdout=out, stderr=err)
    return code, out.value, err.value


class TestTtlValidation:
    """ADV-4: --ttl bounds — 60..31536000 accepted, outside refused."""

    @pytest.mark.parametrize("ttl", [60, 31536000])
    def test_boundary_ttls_accepted(self, scratch, ttl):
        code, out, err = _run(
            [
                "issue-token",
                "--label",
                f"ttl-ok-{ttl}",
                "--scopes",
                "auth:admin",
                "--ttl",
                str(ttl),
            ],
            scratch,
        )
        assert code == 0
        lines = [l for l in out.strip().splitlines() if l]
        # Plaintext printed exactly once; nothing else on stdout.
        assert len(lines) == 1
        assert "issued token" in err
        rows = AuthSessionRepository(scratch).get_bearer_hashes_by_label(
            f"ttl-ok-{ttl}"
        )
        assert len(rows) == 1
        assert rows[0].expires_at > rows[0].created_at

    @pytest.mark.parametrize("ttl", [0, -5, 31536001])
    def test_out_of_range_ttls_refused(self, scratch, ttl):
        code, out, err = _run(
            [
                "issue-token",
                "--label",
                f"ttl-bad-{ttl}",
                "--scopes",
                "auth:admin",
                "--ttl",
                str(ttl),
            ],
            scratch,
        )
        # Non-zero exit AND a clear stderr reason naming the range.
        assert code != 0
        assert "invalid --ttl" in err
        assert "60..31536000" in err
        # NOTHING on stdout (the plaintext crosses stdout exactly once,
        # and a refused mint never gets that far).
        assert out.strip() == ""
        # No row minted — the refusal happens BEFORE the insert.
        assert (
            AuthSessionRepository(scratch).get_bearer_hashes_by_label(
                f"ttl-bad-{ttl}"
            )
            == []
        )

    def test_ttl_omitted_still_uses_config_default(self, scratch):
        # The validation is opt-in on --ttl presence: omitting it keeps
        # the configured default path (already covered P4-side for the
        # value itself; here we pin that omission still mints fine).
        code, out, err = _run(
            ["issue-token", "--label", "ttl-default", "--scopes", "auth:admin"],
            scratch,
        )
        assert code == 0
        assert "issued token" in err
