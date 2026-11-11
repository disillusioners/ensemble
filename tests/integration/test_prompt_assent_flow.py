"""The end-to-end provenance flow (prompt-assent Phase 3 — plan §Test plan).

The FULL evidence chain, driven through the WIRED surface:

    mint (diff + token + render_sha256 shown)
      → the human turn ECHOES the token
        → approve (the tool gate → the attestation → the service →
          the Phase-1 repo entrypoint → the D-4 hook → the guarded
          transition)
          → read the ledger back and reconstruct:
            token ↔ item ↔ message ↔ human ↔ relay agent ↔ time
          ...and prove the raw token appears NOWHERE in durable state.

NOTE: ``tests/integration`` is DESELECTED by the default addopts — run
this file EXPLICITLY:

    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \\
        -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL \\
        -u ENSEMBLE_SELF_ENV \\
    .venv/bin/pytest tests/integration/test_prompt_assent_flow.py \\
        --override-ini="addopts=" -q
"""

from __future__ import annotations

from sqlmodel import Session, col, select

import pytest

from daemon.repositories.prompt_assent.models import PromptAssentToken
from daemon.repositories.prompt_version.models import PromptAuditEvent
from daemon.repositories.prompt_version.repository import (
    ActorContext,
)
from daemon.services.prompt_version_service import PromptVersionService
from daemon.tools.prompt_assent import KILL_SWITCH_ENV
from tests.helpers.prompt_assent_flow import (
    PENDING_CONTENT,
    build_flow,
    decide,
    echo,
    mint_for,
)
from tests.helpers.prompt_assent_tool_fixture import (
    CHANNEL_ID,
    MESSAGE_ID,
    RELAY_INSTANCE_ID,
    SOURCE_ID,
    USER_ID,
)


def _token_of(mint: str) -> str:
    return mint.split("token: ", 1)[1].splitlines()[0]


def _render_sha_of(mint: str) -> str:
    return mint.split("render_sha256: ", 1)[1].splitlines()[0]


@pytest.fixture()
def armed(monkeypatch):
    monkeypatch.setenv(KILL_SWITCH_ENV, "1")
    return build_flow()


class TestTheMintToApproveProvenanceFlow:
    async def test_the_full_chain_is_reconstructable_from_the_ledger(
        self, armed
    ) -> None:
        flow = armed
        mint = await mint_for(flow)
        assert "prompt_assent mint" in mint, mint

        # The mint response SHOWS the exact render and its hash.
        shown_sha = _render_sha_of(mint)
        assert mint.count("--- BEGIN CANONICAL RENDER ---") == 1
        assert PENDING_CONTENT.splitlines()[0] in mint, (
            "the mint must re-show the diff (a bare token tells nobody "
            "anything)"
        )
        token = _token_of(mint)
        assert token.startswith("CONFIRM-")

        # The human turn ECHOES the token in the SAME DM.
        echo(flow, token)
        result = await decide(flow, token=token)
        assert "prompt_assent approve — OK" in result, result

        # ── read the ledger back ─────────────────────────────────────
        with Session(flow.engine) as session:
            token_rows = session.exec(select(PromptAssentToken)).all()
            audit_rows = session.exec(
                select(PromptAuditEvent).where(
                    col(PromptAuditEvent.version_id) == flow.version_id,
                    col(PromptAuditEvent.event_type) == "APPROVED",
                )
            ).all()
        assert len(token_rows) == 1 and len(audit_rows) == 1
        token_row, audit = token_rows[0], audit_rows[0]
        payload = audit.payload

        # token ↔ item
        assert token_row.version_id == flow.version_id
        assert payload["token_id"] == token_row.token_id
        # token ↔ message (the daemon-minted id of the consuming turn)
        assert token_row.consumed_by_message_id == MESSAGE_ID
        assert payload["relay_message_id"] == MESSAGE_ID
        # token ↔ human (the mint-witnessed human)
        assert token_row.mint_user_id == USER_ID
        assert payload["approver_subject"] == f"{SOURCE_ID}:{USER_ID}"
        assert payload["relay_user_id"] == USER_ID
        # message ↔ channel (the mint-witnessed 1:1 channel)
        assert token_row.mint_channel_id == CHANNEL_ID
        assert payload["relay_channel_id"] == CHANNEL_ID
        assert payload["relay_source_id"] == SOURCE_ID
        # human ↔ relay agent ↔ time
        assert payload["relay_agent"] == "ari"
        assert payload["relay_instance_id"] == RELAY_INSTANCE_ID
        assert payload["relay_window_stamped_at"]
        assert payload["assented_at"]
        assert payload["token_fingerprint"] == token_row.token_hash[:12]
        # the shown bytes guarantee is auditable after the fact
        assert payload["render_sha256"] == shown_sha == token_row.render_sha256
        # the item identity
        assert token_row.agent_id == flow.pending_row.agent_id
        assert token_row.artifact == flow.pending_row.artifact
        assert int(token_row.version) == int(flow.pending_row.version)

        # The row is approved and the token consumed.
        assert flow.repo.get_version(flow.version_id).state == "approved"
        assert token_row.status == "consumed"
        assert flow.token_repo.find_live(flow.version_id) is None

    async def test_the_raw_token_appears_nowhere_in_durable_state(
        self, armed
    ) -> None:
        flow = armed
        mint = await mint_for(flow)
        token = _token_of(mint)
        echo(flow, token)
        ok = await decide(flow, token=token)
        assert "prompt_assent approve — OK" in ok, ok

        with Session(flow.engine) as session:
            tokens = session.exec(select(PromptAssentToken)).all()
            audits = session.exec(select(PromptAuditEvent)).all()
        durable = "\n".join(
            str(getattr(row, c, ""))
            for row in [*tokens, *audits]
            for c in vars(row)
        ) + repr([getattr(a, "payload", None) for a in audits])
        variants = {
            token,
            token.replace("-", ""),
            token.replace("CONFIRM-", ""),
            token.replace("-", "").replace("CONFIRM-", ""),
        }
        for variant in variants:
            assert variant not in durable, (
                f"the raw token variant {variant!r} leaked into durable "
                "state"
            )

    async def test_the_reject_mirror_leaves_the_same_chain(
        self, armed
    ) -> None:
        flow = armed
        mint = await mint_for(flow)
        shown_sha = _render_sha_of(mint)
        token = _token_of(mint)
        echo(flow, token)
        result = await decide(flow, action="reject", token=token)
        assert "prompt_assent reject — OK" in result, result
        with Session(flow.engine) as session:
            audit = session.exec(
                select(PromptAuditEvent).where(
                    col(PromptAuditEvent.version_id) == flow.version_id,
                    col(PromptAuditEvent.event_type) == "REJECTED",
                )
            ).first()
        assert audit is not None
        assert audit.payload["reject_reason"] == "not good enough"
        assert len(audit.payload["reject_reason"]) <= 500
        assert audit.payload["render_sha256"] == shown_sha
        assert flow.repo.get_version(flow.version_id).state == "rejected"

    async def test_the_fe_path_still_works_for_the_same_row_type(
        self, armed
    ) -> None:
        """The FE-unchanged property, flow-level: a pending CORE row
        can still be approved by the session lane — while the relay
        lane refuses a non-relay actor."""
        flow = armed
        submitter = flow.submitter
        draft = flow.repo.create_draft(
            agent_id="ari", version_tag="", artifact="soul",
            content="a fe-decided row\n", actor=submitter,
        )
        row = flow.repo.submit_for_approval(draft.version_id, submitter)
        human = ActorContext(subject="operator", auth_method="session")
        approved = flow.repo.approve(row.version_id, human)
        assert approved.state == "approved"

        # And the relay path REFUSES a session actor (the repo's lane
        # gate) — the lanes are distinct.
        from tests.helpers.prompt_assent_fixture import build_attestation

        att = build_attestation(version_id=row.version_id)
        svc = PromptVersionService(repo=flow.repo)
        with pytest.raises(ValueError, match="only assent-relay actors"):
            svc._repo.approve_assent(row.version_id, human, att)
