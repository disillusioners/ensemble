"""Phase-1 token-store tests — schema pins + mint/find/consume/sweep lifecycle.

Covers phase1-plan tasks 1.1 (model) + 1.2 (repository) acceptance:

* schema — ``token_hash`` is a hash column and the raw token has NO
  column anywhere; NO ``token_display``; NO ``content_hash`` /
  ``content_sha256`` (retired naming); NO hard FK to ``prompt_versions``
  (O-B); the partial unique index ``(version_id) WHERE status='live'``
  exists; ZERO ``_ensure_postgres_columns`` entries for the table;
* lifecycle — mint → find → consume → sweep; the raw token is never
  stored; consumed rows are RETAINED by a sweep and still answer
  ``token-already-used``; a re-mint SUPersedes (and the superseded token
  reads ``token-superseded``, NOT ``token-already-used``); mint refuses
  a live token unless forced (``mint-live-token-exists``); the attempt
  counter increments and the lockout revokes at threshold.

The durable-leg PG mirror (own-transaction property, counters under a
forced rollback) lives in ``tests/postgres/test_prompt_assent_token_store_pg.py``.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from sqlalchemy import inspect as sa_inspect

from daemon.repositories.prompt_assent.models import (
    PROMPT_ASSENT_TOKENS_TABLE,
    STATUS_CONSUMED,
    STATUS_LIVE,
    STATUS_REVOKED,
    STATUS_SUPERSEDED,
    PromptAssentToken,
)
from daemon.repositories.prompt_assent.repository import (
    AssentTokenError,
    PromptAssentTokenRepository,
    REVOKE_THRESHOLD_DEFAULT,
)
from tests.helpers.prompt_assent_fixture import (  # noqa: F401 — fixtures
    sha256_hex,
    sqlite_engine,
    token_repo,
)

_RAW_TOKEN = "CONFIRM-AB2DEFGH"
_TOKEN_HASH = sha256_hex(_RAW_TOKEN)


# ─────────────────────────────────────────────────────────────────────────────
# Schema pins (task 1.1 acceptance)
# ─────────────────────────────────────────────────────────────────────────────


class TestTokenSchemaPins:
    def _columns(self):
        return {
            c.name for c in PromptAssentToken.__table__.columns
        }

    def test_token_hash_is_a_column_and_raw_token_is_not(self):
        cols = self._columns()
        assert "token_hash" in cols
        # The raw token's candidate column names must not exist.
        for forbidden in ("token", "raw_token", "token_value", "nonce"):
            assert forbidden not in cols, (
                f"a raw-token column {forbidden!r} appeared — hash-only "
                f"storage (S-3) forbids it"
            )

    def test_no_token_display_column(self):
        assert "token_display" not in self._columns(), (
            "D-1 amendment 1: NO token_display column — the grouped "
            "render minus dashes IS the raw secret"
        )

    def test_retired_hash_naming_absent(self):
        cols = self._columns()
        assert "content_hash" not in cols
        assert "content_sha256" not in cols
        assert "render_sha256" in cols

    def test_no_hard_fk_to_prompt_versions(self):
        # The structural assertion: NO ForeignKey on any column (O-B —
        # version_id is an indexed SOFT reference only).
        all_fks = []
        for col in PromptAssentToken.__table__.columns:
            all_fks.extend(col.foreign_keys)
        assert all_fks == [], (
            "O-B: version_id must be an indexed SOFT reference — no "
            "hard FK to prompt_versions (forensic rows outlive version "
            "archival)"
        )

    def test_partial_unique_index_on_live_version(self):
        indexes = {i.name: i for i in PromptAssentToken.__table__.indexes}
        assert "uq_prompt_assent_tokens_live_version" in indexes
        idx = indexes["uq_prompt_assent_tokens_live_version"]
        assert idx.unique
        assert [c.name for c in idx.columns] == ["version_id"]
        from sqlalchemy.dialects import postgresql, sqlite
        from sqlalchemy.schema import CreateIndex

        pg_ddl = str(CreateIndex(idx).compile(dialect=postgresql.dialect()))
        sq_ddl = str(CreateIndex(idx).compile(dialect=sqlite.dialect()))
        assert "WHERE" in pg_ddl.upper() and "status" in pg_ddl
        assert "WHERE" in sq_ddl.upper() and "status" in sq_ddl

    def test_full_index_name_set_byte_identical_target(self):
        names = {i.name for i in PromptAssentToken.__table__.indexes}
        assert names == {
            "uq_prompt_assent_tokens_token_hash",
            "uq_prompt_assent_tokens_live_version",
            "idx_prompt_assent_tokens_version_status",
        }

    def test_no_ensure_postgres_columns_entry(self):
        """Grep-proven ZERO _ensure_postgres_columns entries (task 1.4)."""
        src = Path(
            "daemon/manager.py"
        ).read_text(encoding="utf-8")
        assert "prompt_assent_tokens" not in src, (
            "a _ensure_postgres_columns entry for prompt_assent_tokens "
            "crept into manager.py — the ensure list is ONE transaction "
            "and any entry's failure ABORTS BOOT (the create_all seam "
            "is the only PG path)"
        )
        # The seam itself must exist in api.py.
        api_src = Path("daemon/api.py").read_text(encoding="utf-8")
        assert "PromptAssentToken.metadata.create_all" in api_src

    def test_factory_registers_the_model(self):
        factory_src = Path(
            "daemon/repositories/factory.py"
        ).read_text(encoding="utf-8")
        assert (
            "from .prompt_assent.models import PromptAssentToken"
            in factory_src
        )


# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle (task 1.2 acceptance)
# ─────────────────────────────────────────────────────────────────────────────


def _mint(token_repo, *, version_id="vid-1", token_hash=_TOKEN_HASH, **kw):
    return token_repo.mint(
        version_id=version_id,
        agent_id="ari",
        version_tag="",
        artifact="rule",
        version=3,
        token_hash=token_hash,
        render_sha256=sha256_hex(f"render:{version_id}"),
        mint_source_id="my-discord-bot",
        mint_channel_id="999888777",
        mint_is_dm=True,
        mint_user_id="112233445566",
        minted_external_user_id="dm:112233445566",
        **kw,
    )


class TestTokenLifecycle:
    def test_mint_find_live_find_by_hash(self, token_repo):
        row = _mint(token_repo)
        assert row.status == STATUS_LIVE
        assert row.token_hash == _TOKEN_HASH
        assert row.token_id and len(row.token_id) == 32  # uuid4 hex
        live = token_repo.find_live("vid-1")
        assert live is not None and live.token_id == row.token_id
        by_hash = token_repo.find_by_hash(_TOKEN_HASH)
        assert by_hash is not None and by_hash.token_id == row.token_id

    def test_mint_sets_ttl_from_imported_ssot(self, token_repo):
        row = _mint(token_repo)
        from daemon.tools.upgrade_journal import NONCE_TTL_S, parse_iso_utc
        from datetime import datetime, timezone

        issued = datetime.strptime(
            row.issued_at, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        expires = datetime.strptime(
            row.ttl_expires_at, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        assert int((expires - issued).total_seconds()) == NONCE_TTL_S

    def test_raw_token_never_stored(self, sqlite_engine, token_repo):
        _mint(token_repo)
        # Hash-only: scan every TEXT column of every row for the raw value.
        from sqlalchemy import text as _text

        with sqlite_engine.connect() as conn:
            rows = conn.execute(
                _text(f"SELECT * FROM {PROMPT_ASSENT_TOKENS_TABLE}")
            ).fetchall()
        assert rows, "mint must persist a row"
        for row in rows:
            assert _RAW_TOKEN not in str(tuple(row)), (
                "the RAW token leaked into durable state (S-3)"
            )
            assert _RAW_TOKEN.replace("-", "") not in str(tuple(row))

    def test_mint_refuses_non_hash(self, token_repo):
        with pytest.raises(ValueError, match="NEVER the raw token"):
            _mint(token_repo, token_hash=_RAW_TOKEN)

    def test_re_mint_without_force_refuses(self, token_repo):
        _mint(token_repo)
        with pytest.raises(AssentTokenError) as exc:
            _mint(token_repo, token_hash=sha256_hex("CONFIRM-SECOND1"))
        assert exc.value.code == "mint-live-token-exists"

    def test_re_mint_supersedes_and_prior_reads_superseded(
        self, token_repo
    ):
        first = _mint(token_repo)
        second = _mint(
            token_repo,
            token_hash=sha256_hex("CONFIRM-SECOND1"),
            force=True,
        )
        assert second.status == STATUS_LIVE
        assert second.token_id != first.token_id
        prior = token_repo.find_by_id(first.token_id)
        assert prior.status == STATUS_SUPERSEDED
        assert prior.superseded_at is not None
        # Exactly ONE live row for the item (the partial index's rule).
        assert len(
            [
                t
                for t in (
                    token_repo.find_by_id(first.token_id),
                    token_repo.find_by_id(second.token_id),
                )
                if t.status == STATUS_LIVE
            ]
        ) == 1
        # The superseded token's replay reads token-SUPERSEDED —
        # deliberately distinct from token-already-used (S-2).
        with pytest.raises(AssentTokenError) as exc:
            token_repo.consume(first.token_id, "msg-late")
        assert exc.value.code == "token-superseded"

    def test_consume_cas_and_already_used(self, token_repo):
        row = _mint(token_repo)
        consumed = token_repo.consume(row.token_id, "msg-approve-1")
        assert consumed.status == STATUS_CONSUMED
        assert consumed.consumed_at is not None
        assert consumed.consumed_by_message_id == "msg-approve-1"
        with pytest.raises(AssentTokenError) as exc:
            token_repo.consume(row.token_id, "msg-replay")
        assert exc.value.code == "token-already-used"
        assert token_repo.find_live("vid-1") is None

    def test_sweep_retains_consumed_rows(self, token_repo):
        row = _mint(token_repo)
        token_repo.consume(row.token_id, "msg-approve-1")
        # Expire the row artificially (consumed ⇒ retained regardless).
        from sqlalchemy import text as _text

        with token_repo._engine.begin() as conn:
            conn.execute(
                _text(
                    f"UPDATE {PROMPT_ASSENT_TOKENS_TABLE} "
                    "SET ttl_expires_at = '2020-01-01T00:00:00Z'"
                )
            )
        deleted = token_repo.sweep()
        assert deleted == 0, "consumed rows survive the sweep (S-2)"
        assert token_repo.find_by_id(row.token_id) is not None
        # And it still answers token-already-used.
        with pytest.raises(AssentTokenError) as exc:
            token_repo.consume(row.token_id, "msg-late")
        assert exc.value.code == "token-already-used"

    def test_sweep_deletes_only_expired_non_consumed(self, token_repo):
        dead = _mint(
            token_repo, version_id="vid-dead",
            token_hash=sha256_hex("CONFIRM-DEAD0001"),
        )
        old_superseded = _mint(
            token_repo, version_id="vid-old",
            token_hash=sha256_hex("CONFIRM-OLDSUP1"),
        )
        _mint(
            token_repo,
            version_id="vid-old",
            token_hash=sha256_hex("CONFIRM-NEWER01"),
            force=True,
        )
        fresh_live = _mint(
            token_repo, version_id="vid-fresh",
            token_hash=sha256_hex("CONFIRM-FRESH01"),
        )
        from sqlalchemy import text as _text

        with token_repo._engine.begin() as conn:
            conn.execute(
                _text(
                    f"UPDATE {PROMPT_ASSENT_TOKENS_TABLE} "
                    "SET ttl_expires_at = '2020-01-01T00:00:00Z' "
                    "WHERE token_id IN (:a, :b)"
                ),
                {"a": dead.token_id, "b": old_superseded.token_id},
            )
        deleted = token_repo.sweep()
        assert deleted == 2
        assert token_repo.find_by_id(dead.token_id) is None
        assert token_repo.find_by_id(old_superseded.token_id) is None
        assert token_repo.find_by_id(fresh_live.token_id) is not None

    def test_record_failed_attempt_increments(self, token_repo):
        row = _mint(token_repo)
        assert token_repo.record_failed_attempt(row.token_id) is False
        again = token_repo.find_by_id(row.token_id)
        assert again.attempts == 1

    def test_lockout_revokes_at_threshold(self, token_repo):
        row = _mint(token_repo)
        for _ in range(REVOKE_THRESHOLD_DEFAULT - 1):
            assert token_repo.record_failed_attempt(row.token_id) is False
        assert (
            token_repo.record_failed_attempt(row.token_id) is True
        ), "the threshold call must report the revoke"
        locked = token_repo.find_by_id(row.token_id)
        assert locked.status == STATUS_REVOKED
        assert locked.attempts == REVOKE_THRESHOLD_DEFAULT

    def test_threshold_is_parameterized_not_hardcoded(self, token_repo):
        row = _mint(token_repo)
        assert token_repo.record_failed_attempt(
            row.token_id, revoke_threshold=2
        ) is False
        assert token_repo.record_failed_attempt(
            row.token_id, revoke_threshold=2
        ) is True
        assert (
            token_repo.find_by_id(row.token_id).status == STATUS_REVOKED
        )

    def test_revoke_only_flips_live(self, token_repo):
        row = _mint(token_repo)
        assert token_repo.revoke(row.token_id) is True
        assert token_repo.revoke(row.token_id) is False
        # A consumed token never flips to revoked (precise status, S-2).
        second = _mint(
            token_repo, version_id="vid-2",
            token_hash=sha256_hex("CONFIRM-BBBBBBBB"),
        )
        token_repo.consume(second.token_id, "msg-1")
        assert token_repo.revoke(second.token_id) is False
        assert (
            token_repo.find_by_id(second.token_id).status
            == STATUS_CONSUMED
        )
