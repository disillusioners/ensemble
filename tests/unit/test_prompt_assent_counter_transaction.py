"""The counter-separate-transaction regression cell (prompt-assent
Phase 3 — the D-4 BLOCKING rule).

The attempt-counter increment and the lockout revocation commit in
their OWN transaction, OUTSIDE the guarded transition, on the refusal
path. The property under regression: **a refusal that rolls back the
guarded transaction still leaves the attempt counter incremented** —
were the counter inside the guarded transaction, every refusal's
rollback would reset it and the lockout would never fire (D-3's
defence-in-depth silently inert).

P1's PG file asserts the property at the REPOSITORY level (the
counter survives a forced rollback of a neighbouring transaction,
against a native PG cluster — ``tests/postgres/
test_prompt_assent_token_store_pg.py``). THIS file asserts the
property at the WIRING level: the tool increments BEFORE the refusal,
the service's guarded transition rolls back afterwards, and the
counter survives the rollback.
"""

from __future__ import annotations

import pytest

from daemon.repositories.prompt_version.repository import (
    actor_from_assent_relay,
    actor_from_instance,
)
from daemon.services.prompt_version_service import (
    AssentRefusalError,
    PromptVersionService,
)
from daemon.tools.prompt_assent import (
    KILL_SWITCH_ENV,
    PROMPT_ASSENT_REVOKE_THRESHOLD,
)


@pytest.fixture(autouse=True)
def _clean_budget():
    """The K-9 counter is in-RAM — every test starts from a clean slate
    (test-order independence across the matrix files)."""
    from daemon.tools.prompt_assent import reset_mismatch_budget

    reset_mismatch_budget()
    yield
    reset_mismatch_budget()

from tests.helpers.prompt_assent_flow import (
    build_flow,
    decide,
    echo,
    mint_for,
)
from tests.helpers.prompt_assent_tool_fixture import (
    FakeMessageRow,
    build_discord_meta,
)


class TestTheCounterSurvivesTheRollback:
    async def test_the_counter_survives_a_guarded_transition_rollback(
        self, monkeypatch
    ) -> None:
        """A mismatch increments the counter (own transaction); the
        NEXT attempt opens the guarded transition, which fails IN the
        transaction (a pending-side mutation ⇒ render divergence ⇒
        rollback); the counter SURVIVES that rollback."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        live = flow.token_repo.find_live(flow.version_id)
        assert live.attempts == 0

        # Leg 1 — a mismatch verdict (own-transaction increment).
        flow.manager._queue_repository._row = FakeMessageRow(
            content=f"yes {token}",
            discord_meta=build_discord_meta(user_id="999999999"),
        )
        mismatch = await decide(flow, token=token)
        assert "reason=token-approver-mismatch" in mismatch, mismatch
        assert (
            flow.token_repo.find_by_id(live.token_id).attempts == 1
        ), "the own-transaction increment committed"

        # Leg 2 — the RIGHT human now approves, but the pending content
        # moved underneath ⇒ the guarded transition fails IN-txn and
        # ROLLS BACK.
        from daemon.repositories.prompt_version.repository import (
            actor_from_instance,
        )

        flow.repo.update_version_content(
            flow.version_id,
            "MUTATED AFTER THE MISMATCH\n",
            actor_from_instance("inst-submitter-1", "ari"),
        )
        echo(flow, token)
        content_refusal = await decide(flow, token=token)
        assert "reason=token-content-mismatch" in content_refusal, (
            content_refusal
        )

        # THE REGRESSION: the counter survived the guarded rollback.
        survived = flow.token_repo.find_by_id(live.token_id)
        assert survived.attempts == 1, (
            "the attempt counter MUST survive a rollback of the guarded "
            "transaction (the D-4 blocking rule — a counter sharing the "
            "guarded transaction would have been reset to 0)"
        )
        assert survived.status == "live"

    async def test_five_mismatches_revoke_even_across_rollbacks(
        self, monkeypatch
    ) -> None:
        """The lockout therefore DOES fire after the threshold — mixed
        verdict shapes: mismatches (counter) + a guarded rollback."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        live = flow.token_repo.find_live(flow.version_id)
        imposter = build_discord_meta(user_id="999999999")

        # 4× imposter mismatches (counter → 4).
        flow.manager._queue_repository._row = FakeMessageRow(
            content=f"yes {token}", discord_meta=imposter
        )
        for _ in range(PROMPT_ASSENT_REVOKE_THRESHOLD - 1):
            await decide(flow, token=token)
        assert flow.token_repo.find_by_id(live.token_id).attempts == (
            PROMPT_ASSENT_REVOKE_THRESHOLD - 1
        )

        # A guarded-transition rollback (pending mutation) on the
        # imposter path — the 5th mismatch verdict fires the LOCKOUT in
        # its own transaction; the state CAS below it never even opens
        # (the refusal precedes the transition).
        flow.repo.update_version_content(
            flow.version_id,
            "MUTATED BEFORE THE FIFTH\n",
            actor_from_instance("inst-submitter-1", "ari"),
        )
        fifth = await decide(flow, token=token)
        assert "reason=token-approver-mismatch" in fifth, fifth
        revoked = flow.token_repo.find_by_id(live.token_id)
        assert revoked.status == "revoked", "the lockout fired"
        assert revoked.attempts == PROMPT_ASSENT_REVOKE_THRESHOLD

    async def test_a_service_level_mismatch_also_counts_before_the_refusal(
        self, monkeypatch
    ) -> None:
        """The service's OWN mismatch-class refusal
        (``token-item-mismatch`` from the attestation binding) increments
        the durable counter BEFORE raising — same own-transaction
        discipline."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        live = flow.token_repo.find_live(flow.version_id)
        from tests.helpers.prompt_assent_fixture import build_attestation

        att = build_attestation(
            version_id="a-different-version",
            token_id=live.token_id,
            raw_token=token,
        )
        svc = PromptVersionService(repo=flow.repo)
        with pytest.raises(AssentRefusalError) as exc:
            await svc.approve_assent("a-different-version", att)
        assert exc.value.code == "token-item-mismatch"
        assert flow.token_repo.find_by_id(live.token_id).attempts == 1, (
            "the counter committed BEFORE the refusal was raised"
        )
        # And the token survives (a refusal never burns).
        assert flow.token_repo.find_by_id(live.token_id).status == "live"
