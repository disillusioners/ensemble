"""The render-binding mutation cells (prompt-assent Phase 3 — J-3/K-7).

A mutation on EITHER side after mint invalidates the outstanding token
(``token-content-mismatch``) and the guarded transition ROLLS BACK —
no state change, no audit row, the token unburned:

* **pending-side mutation** — the pending content moves underneath the
  token;
* **effective-side mutation** — the EFFECTIVE side is included in the
  canonical render, so baseline motion invalidates too (the CAS alone
  cannot see effective-side motion).

The re-check lives ONLY in P1's D-4 hook (O-L) — this file drives it
through the wired service path and asserts the rollback properties.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, col, select

from daemon.repositories.prompt_version.models import PromptAuditEvent
from daemon.repositories.prompt_version.repository import actor_from_instance
from daemon.tools.prompt_assent import KILL_SWITCH_ENV


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


class TestPendingSideMutation:
    async def test_a_pending_mutation_invalidates_and_rolls_back(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        assert "prompt_assent mint" in mint
        token = mint.split("token: ", 1)[1].splitlines()[0]
        live = flow.token_repo.find_live(flow.version_id)

        with Session(flow.engine) as session:
            audit_before = session.exec(
                select(PromptAuditEvent.event_id)
            ).all()

        # The pending content moves UNDERNEATH the token.
        flow.repo.update_version_content(
            flow.version_id,
            "MUTATED AFTER MINT\n",
            actor_from_instance("inst-submitter-1", "ari"),
        )

        echo(flow, token)
        result = await decide(flow, token=token)
        assert "reason=token-content-mismatch" in result, result

        # The rollback: NO state change, NO audit row, token unburned.
        assert (
            flow.repo.get_version(flow.version_id).state == "pending_approval"
        )
        with Session(flow.engine) as session:
            audit_after = session.exec(
                select(PromptAuditEvent.event_id)
            ).all()
        # Exactly ONE new audit row: the CONTENT-mutation's own ledger
        # entry (pre-existing behavior) — NO decision row.
        assert len(audit_after) == len(audit_before) + 1
        after = flow.token_repo.find_by_id(live.token_id)
        assert after.status == "live"
        assert after.attempts == 0, (
            "an in-transaction refusal cannot carry a counter increment "
            "(the D-4 counter never shares the guarded transaction)"
        )


class TestEffectiveSideMutation:
    async def test_baseline_motion_invalidates(self, monkeypatch) -> None:
        """J-3/K-7 — the EFFECTIVE side is in the render: approving a
        newer baseline underneath the token invalidates it (the CAS
        alone cannot see this)."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        assert "prompt_assent mint" in mint
        token = mint.split("token: ", 1)[1].splitlines()[0]
        live = flow.token_repo.find_live(flow.version_id)

        # Move the EFFECTIVE side: approve + select a DIFFERENT row for
        # the same item key, so effective_for_render resolves elsewhere.
        submitter = actor_from_instance("inst-submitter-1", "ari")
        human = type(submitter)(
            subject="operator", auth_method="session"
        )
        baseline = flow.repo.create_draft(
            agent_id="ari",
            version_tag="",
            artifact="rule",
            content="THE BASELINE MOVED\n",
            actor=submitter,
        )
        flow.repo.submit_for_approval(baseline.version_id, submitter)
        approved = flow.repo.approve(baseline.version_id, human)
        flow.repo.set_selection(
            "ari", "", "rule", approved.version_id, human
        )

        echo(flow, token)
        result = await decide(flow, token=token)
        assert "reason=token-content-mismatch" in result, result
        assert (
            flow.repo.get_version(flow.version_id).state == "pending_approval"
        ), "rollback — the pending row is untouched"
        assert flow.token_repo.find_by_id(live.token_id).status == "live"
        with Session(flow.engine) as session:
            decision_rows = session.exec(
                select(PromptAuditEvent).where(
                    col(PromptAuditEvent.version_id) == flow.version_id,
                    col(PromptAuditEvent.event_type) == "APPROVED",
                )
            ).all()
        assert decision_rows == [], "no decision audit row on the rollback"
