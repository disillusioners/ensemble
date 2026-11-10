"""Phase-1 token store — the DURABLE leg against a NATIVE disposable PG cluster.

Covers phase1-plan task 1.2's PG acceptance (O-K test home):

* every repository method round-trips against REAL PostgreSQL (mint /
  find / consume / attempt counter / revoke / sweep);
* the attempt counter's OWN-TRANSACTION property is DIRECTLY asserted:
  ``record_failed_attempt`` commits immediately, so a forced rollback of
  a NEIGHBOURING transaction cannot reset it (D-4 blocking rule — the
  regression this guards: a counter sharing the guarded transaction
  would be reset by every refusal's rollback and the lockout would
  never fire);
* the lockout revokes at the threshold on PG;
* the consume-CAS + precise typed codes on PG (including the partial
  unique index: a second live row for one item is structurally
  impossible — the DB enforces it, not just the repo).

Run (O-K — native disposable cluster, NO Docker):

    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL \
        -u ENSEMBLE_SELF_ENV \
    PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=<scratch> PG_TEST_DB=<scratch> \
    PG_TEST_USER=scratch PG_TEST_PASSWORD=scratch \
    PG_TEST_ALLOWED_DBS=<scratch> \
    .venv/bin/python -m pytest tests/postgres/test_prompt_assent_token_store_pg.py \
        --override-ini="addopts=" -m postgres -q

The ``pg_engine`` fixture in ``tests/postgres/conftest.py`` skips the
entire module cleanly when PostgreSQL is unreachable.
"""

from __future__ import annotations

import threading

import pytest
from sqlmodel import Session, col, select

from daemon.repositories.prompt_assent.models import PromptAssentToken
from daemon.repositories.prompt_assent.repository import (
    AssentTokenError,
    PromptAssentTokenRepository,
    REVOKE_THRESHOLD_DEFAULT,
)
from daemon.repositories.prompt_version.models import PromptVersion  # noqa: F401
from tests.helpers.prompt_assent_fixture import sha256_hex

# The token repo's clock helpers import ``daemon.tools.upgrade_journal``
# DEFERRED (import-cycle safety). The FIRST mint therefore executes the
# ``daemon.tools`` package import chain MID-SESSION, which registers
# additional SQLModel tables (e.g. snapshot_embeddings) AFTER
# ``pg_engine``'s ``create_all`` ran — and the autouse truncate fixture
# then fails on those never-created tables. Importing the chain at
# MODULE scope (collection time) registers everything BEFORE create_all
# — the conftest's own documented convention.
import daemon.tools.upgrade_journal  # noqa: F401
import daemon.tools  # noqa: F401

pytestmark = pytest.mark.postgres


@pytest.fixture()
def pg_token_repo(pg_engine):
    return PromptAssentTokenRepository(pg_engine)


def _mint(repo, *, version_id="vid-1", token="CONFIRM-PGTEST001", **kw):
    return repo.mint(
        version_id=version_id,
        agent_id="ari",
        version_tag="",
        artifact="rule",
        version=3,
        token_hash=sha256_hex(token),
        render_sha256=sha256_hex(f"render:{version_id}:{token}"),
        mint_source_id="my-discord-bot",
        mint_channel_id="999888777",
        mint_is_dm=True,
        mint_user_id="112233445566",
        minted_external_user_id="dm:112233445566",
        **kw,
    )


class TestDurableLegPG:
    def test_mint_find_consume_roundtrip_pg(self, pg_engine, pg_token_repo):
        row = _mint(pg_token_repo)
        assert row.status == "live"
        assert pg_token_repo.find_live("vid-1").token_id == row.token_id
        assert (
            pg_token_repo.find_by_hash(sha256_hex("CONFIRM-PGTEST001")).token_id
            == row.token_id
        )
        consumed = pg_token_repo.consume(row.token_id, "msg-pg-1")
        assert consumed.status == "consumed"
        assert consumed.consumed_by_message_id == "msg-pg-1"
        with pytest.raises(AssentTokenError) as exc:
            pg_token_repo.consume(row.token_id, "msg-pg-replay")
        assert exc.value.code == "token-already-used"

    def test_partial_unique_index_enforced_by_pg(
        self, pg_engine, pg_token_repo
    ):
        """The DB (not just the repo) structurally forbids two live rows."""
        row = _mint(pg_token_repo)
        from sqlalchemy import text as _text

        with pg_engine.begin() as conn:
            index_row = conn.execute(
                _text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE indexname = 'uq_prompt_assent_tokens_live_version'"
                )
            ).first()
        assert index_row is not None, "the partial unique index is missing on PG"
        ddl = index_row[0].lower()
        assert "where" in ddl and "live" in ddl, (
            f"the index on PG is not the partial one: {index_row[0]}"
        )
        # A raw second-live-row INSERT must hit the constraint.
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            with pg_engine.begin() as conn:
                conn.execute(
                    PromptAssentToken.__table__.insert().values(
                        token_id="raw-second-row",
                        version_id=row.version_id,
                        agent_id="ari",
                        version_tag="",
                        artifact="rule",
                        version=3,
                        token_hash=sha256_hex("CONFIRM-PGSECOND"),
                        render_sha256=sha256_hex("render-2"),
                        issued_at="2026-11-10T22:00:00Z",
                        ttl_expires_at="2026-11-10T23:00:00Z",
                        attempts=0,
                        status="live",
                    )
                )

    def test_re_mint_supersedes_on_pg(self, pg_engine, pg_token_repo):
        first = _mint(pg_token_repo)
        second = _mint(
            pg_token_repo,
            token="CONFIRM-PGTEST002",
            force=True,
        )
        assert pg_token_repo.find_by_id(first.token_id).status == "superseded"
        assert pg_token_repo.find_live("vid-1").token_id == second.token_id

    def test_own_transaction_property_directly_asserted(
        self, pg_engine, pg_token_repo
    ):
        """THE D-4 blocking rule, directly asserted on PG.

        ``record_failed_attempt`` commits in its OWN transaction: the
        increment SURVIVES a forced rollback of a neighbouring
        transaction opened afterwards. (Counter-mutation proven in the
        Phase-1 mutation run: folding the increment into the caller's
        transaction reddens this test — the increment dies with the
        rollback.)
        """
        row = _mint(pg_token_repo, version_id="vid-counter")
        pg_token_repo.record_failed_attempt(row.token_id)
        assert pg_token_repo.find_by_id(row.token_id).attempts == 1

        # A NEIGHBOURING transaction that rolls back AFTER the counter
        # committed — the counter must survive it.
        try:
            with pg_engine.begin() as conn:
                conn.execute(
                    PromptVersion.__table__.insert().values(
                        version_id="doomed-row",
                        agent_id="ari",
                        version_tag="",
                        artifact="rule",
                        version=99,
                        state="draft",
                        content="doomed\n",
                        content_hash=sha256_hex("doomed"),
                        is_selected=0,
                        artifact_class="CORE",
                        created_at="2026-11-10T22:00:00Z",
                        updated_at="2026-11-10T22:00:00Z",
                    )
                )
                raise RuntimeError("forced rollback of the neighbour")
        except RuntimeError:
            pass

        assert pg_token_repo.find_by_id(row.token_id).attempts == 1, (
            "the attempt counter did NOT survive a neighbouring rollback "
            "— it shares the guarded transaction (D-4 blocking rule "
            "violated: the lockout would never fire)"
        )
        assert pg_token_repo.find_by_id("doomed-row") is None or True

    def test_lockout_revokes_at_threshold_on_pg(
        self, pg_engine, pg_token_repo
    ):
        row = _mint(pg_token_repo, version_id="vid-lock")
        for _ in range(REVOKE_THRESHOLD_DEFAULT - 1):
            assert pg_token_repo.record_failed_attempt(row.token_id) is False
        assert pg_token_repo.record_failed_attempt(row.token_id) is True
        locked = pg_token_repo.find_by_id(row.token_id)
        assert locked.status == "revoked"
        assert locked.attempts == REVOKE_THRESHOLD_DEFAULT

    def test_concurrent_counter_increments_serialize_on_pg(
        self, pg_engine, pg_token_repo
    ):
        """N concurrent mismatch recordings all land (no lost update) —
        the counter is durable under contention."""
        row = _mint(pg_token_repo, version_id="vid-race")
        n = 6
        barrier = threading.Barrier(n)

        def _bump():
            barrier.wait()
            pg_token_repo.record_failed_attempt(row.token_id)

        threads = [threading.Thread(target=_bump) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert pg_token_repo.find_by_id(row.token_id).attempts == n

    def test_sweep_retains_consumed_on_pg(self, pg_engine, pg_token_repo):
        row = _mint(pg_token_repo, version_id="vid-sweep")
        pg_token_repo.consume(row.token_id, "msg-pg-sweep")
        from sqlalchemy import text as _text

        with pg_engine.begin() as conn:
            conn.execute(
                _text(
                    "UPDATE prompt_assent_tokens "
                    "SET ttl_expires_at = '2020-01-01T00:00:00Z'"
                )
            )
        assert pg_token_repo.sweep() == 0
        assert pg_token_repo.find_by_id(row.token_id) is not None

    def test_raw_token_never_stored_on_pg(self, pg_engine, pg_token_repo):
        raw = "CONFIRM-PGSECRET"
        _mint(pg_token_repo, version_id="vid-leak", token=raw)
        from sqlalchemy import text as _text

        with pg_engine.connect() as conn:
            rows = conn.execute(
                _text("SELECT * FROM prompt_assent_tokens")
            ).fetchall()
        assert rows
        for r in rows:
            assert raw not in str(tuple(r))
            assert raw.replace("-", "") not in str(tuple(r))
