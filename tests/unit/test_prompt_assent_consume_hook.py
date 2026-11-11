"""Phase-1 D-4 consume-hook tests (task 1.11 + O-L ordering).

Covers phase1-plan task 1.11 acceptance:

* the hook is DEFAULT-ABSENT ⇒ the FE path is provably untouched (the
  plain session approve works exactly as before, no hook executed);
* the hook runs INSIDE the transition's transaction: a render-hash
  divergence rolls the STATE CHANGE (and the audit row) back with it —
  ``token-content-mismatch``, nothing persists;
* the consume-CAS refuses a non-live token with the precise typed code;
* NO forked CAS+audit copy exists (structural pin on
  ``atomic_transition``: ONE transaction block, ONE audit-append call
  site, ONE CAS-update site — the hook is invoked, not duplicated);
* the O-L ordering: the hook reads the row PRE-CAS (the re-render must
  match the mint-time semantics — the row's own in-txn approval must
  not make it its own baseline).
"""

from __future__ import annotations

import inspect
import json

import pytest
from sqlmodel import Session, col, select

from daemon.repositories.prompt_assent.consume_hook import (
    build_assent_consume_hook,
)
from daemon.repositories.prompt_assent.repository import AssentTokenError
from daemon.repositories.prompt_version.models import PromptAuditEvent
from daemon.repositories.prompt_version.repository import (
    ActorContext,
    AUTH_METHOD_SESSION,
    actor_from_instance,
)
from daemon.services.prompt_version_diff import render_sha256
from tests.helpers.prompt_assent_fixture import (  # noqa: F401 — fixtures
    PENDING_CONTENT,
    build_attestation,
    build_relay_actor,
    pending_row,
    pv_repo,
    sha256_hex,
    sqlite_engine,
    token_repo,
)

RAW_TOKEN = "CONFIRM-HOOK0001"


def _mint_with_real_render(pv_repo, pending_row, token_repo):  # noqa: F811
    eff = pv_repo.effective_for_render(
        pending_row.agent_id, pending_row.version_tag, pending_row.artifact
    )
    mint_sha = render_sha256(
        agent_id=pending_row.agent_id,
        version_tag=pending_row.version_tag,
        artifact=pending_row.artifact,
        version=pending_row.version,
        version_id=pending_row.version_id,
        pending_content=pending_row.content,
        effective_content=eff.content or "",
        effective_version_id=eff.version_id,
    )
    row = token_repo.mint(
        version_id=pending_row.version_id,
        agent_id=pending_row.agent_id,
        version_tag=pending_row.version_tag,
        artifact=pending_row.artifact,
        version=pending_row.version,
        token_hash=sha256_hex(RAW_TOKEN),
        render_sha256=mint_sha,
    )
    att = build_attestation(
        version_id=pending_row.version_id,
        token_id=row.token_id,
        raw_token=RAW_TOKEN,
        render_sha256=mint_sha,
    )
    hook = build_assent_consume_hook(
        token_repo=token_repo,
        pv_repo=pv_repo,
        token_id=row.token_id,
        consumed_by_message_id="msg-hook-1",
    )
    return row, att, build_relay_actor(att), hook


class TestHookDefaultAbsent:
    def test_plain_session_approve_untouched(self, pv_repo):  # noqa: F811
        """The FE path (no hook) transitions exactly as before — and no
        token table access happens at all."""
        submitter = actor_from_instance("inst-1", "ari")
        row = pv_repo.create_draft(
            agent_id="dev", version_tag="", artifact="tools_note",
            content="tools note\n", actor=submitter,
        )
        pv_repo.submit_for_approval(row.version_id, submitter)
        human = ActorContext(subject="h", auth_method=AUTH_METHOD_SESSION)
        out = pv_repo.approve(row.version_id, human)
        assert out.state == "approved"
        with Session(pv_repo.engine) as session:
            event = session.exec(
                select(PromptAuditEvent).where(
                    col(PromptAuditEvent.version_id) == row.version_id,
                    col(PromptAuditEvent.event_type) == "APPROVED",
                )
            ).first()
        assert set(event.payload.keys()) == {
            "from_state", "to_state", "approver_subject",
            "approver_auth_method",
        }

    def test_hook_param_is_keyword_only_private_default_none(self):
        sig = inspect.signature(
            pv_repo_cls().atomic_transition
        )
        params = sig.parameters
        for name in ("_assent_consume_hook", "_extra_audit_payload"):
            assert name in params, f"missing private kwarg {name}"
            p = params[name]
            assert p.kind is inspect.Parameter.KEYWORD_ONLY
            assert p.default is None


def pv_repo_cls():
    from daemon.repositories.prompt_version.repository import (
        PromptVersionRepository,
    )

    return PromptVersionRepository


class TestHookInTransaction:
    def test_render_divergence_rolls_everything_back(
        self, pv_repo, pending_row, token_repo, sqlite_engine  # noqa: F811
    ):
        """Mutate the pending content AFTER mint ⇒ the in-txn re-render
        diverges ⇒ raise ⇒ the state change AND the audit row roll back
        (O-L: one transaction, all-or-nothing)."""
        row, att, relay, hook = _mint_with_real_render(
            pv_repo, pending_row, token_repo
        )
        before = self._event_types(sqlite_engine)

        # Mutate the pending content UNDERNEATH the token (the repo-level
        # content mutation path allows pending-row edits; the tool gate
        # would normally refuse, but the binding lives at THIS layer).
        pv_repo.update_version_content(
            pending_row.version_id, "MUTATED AFTER MINT\n",
            actor_from_instance("inst-1", "ari"),
        )
        # Sanity: the content mutation itself ledgered (pre-existing
        # behavior — not part of the transition we are about to refuse).
        after_mutation = self._event_types(sqlite_engine)
        assert len(after_mutation) == len(before) + 1

        with pytest.raises(AssentTokenError) as exc:
            pv_repo.approve_assent(
                pending_row.version_id, relay, att, _consume_hook=hook
            )
        assert exc.value.code == "token-content-mismatch"
        # NOTHING from the transition persisted: state unchanged, no
        # APPROVED audit row, token still live.
        assert (
            pv_repo.get_version(pending_row.version_id).state
            == "pending_approval"
        )
        assert self._event_types(sqlite_engine) == after_mutation, (
            "the refused transition leaked an audit row — the rollback "
            "did not cover the co-transactional audit INSERT"
        )
        assert token_repo.find_by_id(row.token_id).status == "live"

    @staticmethod
    def _event_types(engine) -> list[tuple[str, str]]:
        with Session(engine) as session:
            rows = session.exec(
                select(
                    col(PromptAuditEvent.event_id),
                    col(PromptAuditEvent.event_type),
                )
            ).all()
        return [(r[0], r[1]) for r in rows]

    def test_hook_runs_before_state_cas_and_consumes_together(
        self, pv_repo, pending_row, token_repo, sqlite_engine  # noqa: F811
    ):
        """Happy path: one transaction lands state + audit + consume."""
        row, att, relay, hook = _mint_with_real_render(
            pv_repo, pending_row, token_repo
        )
        before_events = len(self._event_types(sqlite_engine))
        out = pv_repo.approve_assent(
            pending_row.version_id, relay, att, _consume_hook=hook
        )
        assert out.state == "approved"
        assert len(self._event_types(sqlite_engine)) == before_events + 1
        consumed = token_repo.find_by_id(row.token_id)
        assert consumed.status == "consumed"
        assert consumed.consumed_by_message_id == "msg-hook-1"

    def test_consume_cas_refuses_non_live_token(self, token_repo):  # noqa: F811
        row = token_repo.mint(
            version_id="vid-x", agent_id="ari", version_tag="",
            artifact="rule", version=1,
            token_hash=sha256_hex("CONFIRM-CONSUME1"),
            render_sha256=sha256_hex("render-x"),
        )
        token_repo.consume(row.token_id, "msg-first")
        with pytest.raises(AssentTokenError) as exc:
            token_repo.consume(row.token_id, "msg-replay")
        assert exc.value.code == "token-already-used"

        # A superseded token reads token-SUPERSEDED — deliberately
        # distinct from token-already-used (S-2).
        superseded = token_repo.mint(
            version_id="vid-y", agent_id="ari", version_tag="",
            artifact="rule", version=1,
            token_hash=sha256_hex("CONFIRM-CONSUME3"),
            render_sha256=sha256_hex("render-y"),
        )
        token_repo.mint(
            version_id="vid-y", agent_id="ari", version_tag="",
            artifact="rule", version=1,
            token_hash=sha256_hex("CONFIRM-CONSUME4"),
            render_sha256=sha256_hex("render-y"),
            force=True,
        )
        with pytest.raises(AssentTokenError) as exc:
            token_repo.consume(superseded.token_id, "msg-again")
        assert exc.value.code == "token-superseded"

    def test_hook_consume_refuses_non_live_inside_transition(
        self, pv_repo, pending_row, token_repo, sqlite_engine  # noqa: F811
    ):
        """The in-txn consume-CAS refuses a token that went non-live
        after the gate read it — the precise code surfaces, nothing
        persists."""
        row, att, relay, _hook = _mint_with_real_render(
            pv_repo, pending_row, token_repo
        )
        # Supersede the token BEFORE the transition (simulates a
        # gate-read→transition race).
        token_repo.mint(
            version_id=pending_row.version_id,
            agent_id=pending_row.agent_id,
            version_tag=pending_row.version_tag,
            artifact=pending_row.artifact,
            version=pending_row.version,
            token_hash=sha256_hex("CONFIRM-RACER001"),
            render_sha256=row.render_sha256,
            force=True,
        )
        before = self._event_types(sqlite_engine)
        with pytest.raises(AssentTokenError) as exc:
            pv_repo.approve_assent(
                pending_row.version_id, relay, att,
                _consume_hook=build_assent_consume_hook(
                    token_repo=token_repo,
                    pv_repo=pv_repo,
                    token_id=row.token_id,
                    consumed_by_message_id="msg-race",
                ),
            )
        assert exc.value.code == "token-superseded"
        assert self._event_types(sqlite_engine) == before
        assert (
            pv_repo.get_version(pending_row.version_id).state
            == "pending_approval"
        )

    def test_already_decided_race_rolls_the_consume_back(
        self, pv_repo, token_repo, sqlite_engine  # noqa: F811
    ):
        """A concurrent FE decision wins the CAS; the relayed path loses
        ⇒ the whole txn (consume included) rolls back — the losing chat
        path leaves its token unconsumed (§5)."""
        submitter = actor_from_instance("inst-1", "ari")
        row = pv_repo.create_draft(
            agent_id="ari", version_tag="", artifact="rule",
            content=PENDING_CONTENT, actor=submitter,
        )
        pv_repo.submit_for_approval(row.version_id, submitter)
        token_row, att, relay, hook = _mint_with_real_render(
            pv_repo, row, token_repo
        )
        human = ActorContext(subject="emperor", auth_method=AUTH_METHOD_SESSION)
        pv_repo.approve(row.version_id, human)  # FE wins the race
        with pytest.raises(Exception):
            pv_repo.approve_assent(
                row.version_id, relay, att, _consume_hook=hook
            )
        assert token_repo.find_by_id(token_row.token_id).status == "live", (
            "the losing relay path must NOT burn its token"
        )


class TestNoForkedCopy:
    def test_atomic_transition_has_one_txn_one_audit_one_cas(self):
        """Structural pin: the hook is INVOKED, not duplicated — ONE
        engine.begin() block, ONE _append_audit_in_conn call site, ONE
        state-CAS update site inside atomic_transition."""
        src = inspect.getsource(pv_repo_cls().atomic_transition)
        assert src.count("engine.begin()") == 1, (
            "a second transaction block appeared in atomic_transition — "
            "forked CAS+audit copy"
        )
        assert src.count("_append_audit_in_conn(") == 1
        assert (
            src.count("PromptVersion.__table__.update()") == 1
        ), "a second state-CAS appeared — forked copy"
        assert src.count("_assent_consume_hook(conn, version_id)") == 1, (
            "the hook must be invoked exactly once"
        )
