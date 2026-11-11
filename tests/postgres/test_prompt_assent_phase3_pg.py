"""Phase-3 PG durable leg — the COUNTER WIRING against a native PG cluster.

The Phase-3 wiring (tool mismatch → ``record_failed_attempt`` in its
OWN transaction → the service's guarded transition) re-proven against
REAL PostgreSQL:

1. the tool-path mismatch increments the durable counter and the
   lockout revokes at the threshold ON PG;
2. the counter SURVIVES a rollback of the guarded transaction (the
   D-4 blocking rule — the in-txn render divergence rolls the
   transition back; the counter stays).

Run (O-K — native disposable cluster, NO Docker):

    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \\
        -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL \\
        -u ENSEMBLE_SELF_ENV \\
    PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=<port> PG_TEST_DB=<scratch> \\
    PG_TEST_USER=<user> PG_TEST_PASSWORD=<pw> PG_TEST_ALLOWED_DBS=<scratch> \\
    .venv/bin/pytest tests/postgres/test_prompt_assent_phase3_pg.py \\
        --override-ini="addopts=" -m postgres -q
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, col, select

from daemon.repositories.prompt_assent.models import PromptAssentToken
from daemon.repositories.prompt_version.models import PromptAuditEvent
from daemon.repositories.prompt_version.repository import (
    ActorContext,
    actor_from_instance,
)
from daemon.services.prompt_version_service import PromptVersionService
from daemon.tools.prompt_assent import PROMPT_ASSENT_REVOKE_THRESHOLD
from tests.helpers.prompt_assent_flow import build_flow, decide, mint_for
from tests.helpers.prompt_assent_tool_fixture import (
    FakeMessageRow,
    build_discord_meta,
)

# See test_prompt_assent_token_store_pg.py: the deferred daemon.tools
# import chain must be executed at MODULE scope (before create_all).
import daemon.tools  # noqa: F401,E402
import daemon.tools.upgrade_journal  # noqa: F401,E402

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def _arm_lane(monkeypatch):
    """The lane is default-OFF (D-2) — arm it for every PG leg."""
    monkeypatch.setenv("ENSEMBLE_PROMPT_ASSENT_ENABLED", "1")


class TestCounterWiringPG:
    async def test_mismatch_counter_and_lockout_on_pg(self, pg_engine) -> None:
        flow = build_flow(engine=pg_engine)
        mint = await mint_for(flow)
        assert "prompt_assent mint" in mint, mint
        token = mint.split("token: ", 1)[1].splitlines()[0]
        live = flow.token_repo.find_live(flow.version_id)
        # An imposter in the SAME 1:1 channel — every verdict is an
        # approver mismatch (the durable counter's path).
        flow.manager._queue_repository._row = FakeMessageRow(
            content=f"yes {token}",
            discord_meta=build_discord_meta(user_id="999999999"),
        )
        for i in range(PROMPT_ASSENT_REVOKE_THRESHOLD):
            result = await decide(flow, token=token)
            assert "reason=token-approver-mismatch" in result, (i, result)
        with Session(pg_engine) as session:
            row = session.get(PromptAssentToken, live.token_id)
            session.expire(row)
            assert row.attempts == PROMPT_ASSENT_REVOKE_THRESHOLD
            assert row.status == "revoked", "the lockout revoked on PG"

    async def test_the_counter_survives_a_transition_rollback_on_pg(
        self, pg_engine
    ) -> None:
        flow = build_flow(engine=pg_engine)
        mint = await mint_for(flow)
        assert "prompt_assent mint" in mint, mint
        token = mint.split("token: ", 1)[1].splitlines()[0]
        live = flow.token_repo.find_live(flow.version_id)

        # Leg 1 — a mismatch (own-transaction increment).
        flow.manager._queue_repository._row = FakeMessageRow(
            content=f"yes {token}",
            discord_meta=build_discord_meta(user_id="999999999"),
        )
        mismatch = await decide(flow, token=token)
        assert "reason=token-approver-mismatch" in mismatch, mismatch

        # Leg 2 — a pending-side mutation ⇒ the guarded transition
        # fails IN-txn (render divergence) and ROLLS BACK.
        flow.repo.update_version_content(
            flow.version_id,
            "MUTATED AFTER THE MISMATCH\n",
            actor_from_instance("inst-submitter-1", "ari"),
        )
        echo_ok = FakeMessageRow(
            content=f"approved — {token}",
            discord_meta=build_discord_meta(),
        )
        flow.manager._queue_repository._row = echo_ok
        content_refusal = await decide(flow, token=token)
        assert "reason=token-content-mismatch" in content_refusal, (
            content_refusal
        )

        # THE REGRESSION — the counter survived the PG rollback.
        with Session(pg_engine) as session:
            row = session.get(PromptAssentToken, live.token_id)
            session.expire(row)
            assert row.attempts == 1, (
                "the counter MUST survive a rollback of the guarded "
                "transaction (D-4 blocking rule)"
            )
            assert row.status == "live"
            events = session.exec(
                select(PromptAuditEvent).where(
                    col(PromptAuditEvent.version_id) == flow.version_id,
                    col(PromptAuditEvent.event_type) == "APPROVED",
                )
            ).all()
            assert events == [], "no decision row leaked from the rollback"
