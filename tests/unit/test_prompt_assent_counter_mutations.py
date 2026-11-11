"""The SIX named counter-mutations + the column-source pin (task 3.8).

Every load-bearing matrix cell carries a NAMED counter-mutation that
DEMONSTRABLY REDDENS it (the house mutation rule — absence-only and
raw-text pins are prose-satisfiable and prove nothing). The evidence
THIS file produces: for each mutation, the cell's own pin assertion is
run (i) UNMUTATED — it passes — and (ii) under the mutation — it
RAISES AssertionError. The AssertionError under mutation IS the
reddening evidence.

| # | Cell | Mutation (named) |
|---|------|------------------|
| 1 | happy path / token-in-args-only | **remove the message-exactness check** (factor 3g) |
| 2 | wrong token / lockout | **move the counter into the guarded transaction** (a rollback-erased increment) |
| 3 | expired token | **break the TTL comparison** |
| 4 | non-pending row | **drop the state predicate from the CAS** |
| 5 | agent-self-approval | **a non-user-origin dispatch leaves the window alive** |
| 6 | non-user-origin turn | **fork/re-derive the classifier** (the unforked-reuse pin) |
| + | mint (K-7 column-source pin) | **feed the column from prompt_versions.content_hash** |

The single most important pin is #1: with the message-exactness check
removed, a token present ONLY in the tool arguments is ACCEPTED — the
authority would no longer be bound to the human's own message.
"""

from __future__ import annotations

import hashlib
import re
from types import SimpleNamespace

import pytest
from sqlmodel import Session, col, select
from sqlalchemy import text

from daemon.manager import InstanceManager
from daemon.repositories.prompt_assent.models import PromptAssentToken
from daemon.repositories.prompt_assent.repository import (
    PromptAssentTokenRepository,
)
from daemon.repositories.prompt_version.models import PromptAuditEvent
from daemon.repositories.prompt_version.repository import (
    PromptVersionRepository,
    actor_from_assent_relay,
    actor_from_instance,
)
from daemon.tools.prompt_assent import (
    KILL_SWITCH_ENV,
    PROMPT_ASSENT_REVOKE_THRESHOLD,
    _classify_window_source,
)


@pytest.fixture(autouse=True)
def _clean_budget():
    """The K-9 counter is in-RAM — every test starts from a clean slate
    (test-order independence across the matrix files)."""
    from daemon.tools.prompt_assent import reset_mismatch_budget

    reset_mismatch_budget()
    yield
    reset_mismatch_budget()

from tests.helpers.prompt_assent_fixture import (  # noqa: F401 — fixtures
    pending_row,
    pv_repo,
    sqlite_engine,
)
from tests.helpers.prompt_assent_flow import (
    build_flow,
    decide,
    echo,
    mint_for,
)
from tests.helpers.prompt_assent_tool_fixture import (
    RELAY_INSTANCE_ID,
    FakeMessageRow,
    build_discord_meta,
    build_window,
)

UNKNOWN_TOKEN = "CONFIRM-ZZZZZZZZ"


def _decision_rows(engine, version_id=None):
    with Session(engine) as session:
        stmt = select(PromptAuditEvent).where(
            col(PromptAuditEvent.event_type) == "APPROVED"
        )
        if version_id is not None:
            stmt = stmt.where(col(PromptAuditEvent.version_id) == version_id)
        return session.exec(stmt).all()


# ═════════════════════════════════════════════════════════════════════
# Mutation 1 — remove the message-exactness check (factor 3g)
# ═════════════════════════════════════════════════════════════════════


class TestMutation1MessageExactness:
    """THE SINGLE MOST IMPORTANT PIN in the whole set."""

    async def test_the_cell_is_green_without_the_mutation(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        assert "prompt_assent mint" in mint
        token = mint.split("token: ", 1)[1].splitlines()[0]
        # The token is in the ARGS only — the triggering message does
        # NOT carry it.
        result = await decide(flow, token=token)
        assert "reason=token-content-mismatch" in result, result

    async def test_removing_the_message_exactness_check_reddens_the_cell(
        self, monkeypatch
    ) -> None:
        """Mutation: factor 3g accepts ANY content (the check is gone).
        The token-only-in-args call is then ACCEPTED all the way to an
        approved row ⇒ the cell's pin (refusal + untouched row) REDS."""
        import daemon.tools.upgrade_journal as uj

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        monkeypatch.setattr(
            uj, "nonce_in_content", lambda stored, content: True
        )
        flow = build_flow()
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        echo(flow, UNKNOWN_TOKEN)  # the message carries a WRONG token
        result = await decide(flow, token=token)
        with pytest.raises(AssertionError) as reddened:
            assert "reason=token-content-mismatch" in result, result
            assert (
                flow.repo.get_version(flow.version_id).state
                == "pending_approval"
            )
        # The mutation evidence: the forged call was ACCEPTED — the row
        # is approved and the decision ledger carries the row.
        assert flow.repo.get_version(flow.version_id).state == "approved"
        assert len(_decision_rows(flow.engine, flow.version_id)) == 1
        assert "token-content-mismatch" not in result


# ═════════════════════════════════════════════════════════════════════
# Mutation 2 — move the counter into the guarded transaction
# ═════════════════════════════════════════════════════════════════════


class TestMutation2CounterInsideTheGuardedTransaction:
    def _rollback_erased_counter(self, repo, token_id, threshold=5):
        """The mutated counter: the increment happens inside a
        transaction that then ROLLS BACK — the "counter inside the
        guarded transaction" outcome (every refusal's rollback resets
        it)."""

        def mutated(tid, revoke_threshold=threshold):
            try:
                with repo._engine.begin() as conn:
                    row = conn.execute(
                        PromptAssentToken.__table__.select().where(
                            col(PromptAssentToken.token_id) == tid
                        )
                    ).first()
                    conn.execute(
                        PromptAssentToken.__table__.update()
                        .where(col(PromptAssentToken.token_id) == tid)
                        .values(attempts=int(row.attempts) + 1)
                    )
                    raise RuntimeError("simulated guarded-transaction rollback")
            except RuntimeError:
                pass
            return False

        return mutated

    async def test_the_lockout_cell_is_green_without_the_mutation(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        live = flow.token_repo.find_live(flow.version_id)
        flow.manager._queue_repository._row = FakeMessageRow(
            content=f"yes {token}",
            discord_meta=build_discord_meta(user_id="999999999"),
        )
        for _ in range(PROMPT_ASSENT_REVOKE_THRESHOLD):
            await decide(flow, token=token)
        assert flow.token_repo.find_by_id(live.token_id).status == "revoked"
        echo(flow, token)
        result = await decide(flow, token=token)
        assert "reason=token-locked" in result, result

    async def test_moving_the_counter_inside_the_transition_reddens(
        self, monkeypatch
    ) -> None:
        """Mutation: the increment commits only inside a transaction a
        refusal rolls back ⇒ the counter never accumulates ⇒ the lockout
        NEVER fires ⇒ the lockout cell REDS."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")

        def _counter_inside_rolled_back_txn(
            self, token_id, revoke_threshold=5
        ):
            """The MUTATED counter: the increment runs inside a
            transaction that then ROLLS BACK — the "counter inside the
            guarded transaction" outcome (never production code)."""
            try:
                with self._engine.begin() as conn:
                    row = conn.execute(
                        PromptAssentToken.__table__.select().where(
                            col(PromptAssentToken.token_id) == token_id
                        )
                    ).first()
                    conn.execute(
                        PromptAssentToken.__table__.update()
                        .where(col(PromptAssentToken.token_id) == token_id)
                        .values(attempts=int(row.attempts) + 1)
                    )
                    raise RuntimeError("simulated guarded-rollback")
            except RuntimeError:
                pass  # the rollback erased the increment
            return False

        monkeypatch.setattr(
            PromptAssentTokenRepository,
            "record_failed_attempt",
            _counter_inside_rolled_back_txn,
        )
        flow = build_flow()
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        live = flow.token_repo.find_live(flow.version_id)
        flow.manager._queue_repository._row = FakeMessageRow(
            content=f"yes {token}",
            discord_meta=build_discord_meta(user_id="999999999"),
        )
        lockout_seen = False
        for _ in range(PROMPT_ASSENT_REVOKE_THRESHOLD + 2):
            result = await decide(flow, token=token)
            if "reason=token-locked" in result:
                lockout_seen = True
        with pytest.raises(AssertionError) as reddened:
            assert lockout_seen, "the lockout must fire after 5 mismatches"
        assert flow.token_repo.find_by_id(live.token_id).status == "live"
        assert flow.token_repo.find_by_id(live.token_id).attempts < (
            PROMPT_ASSENT_REVOKE_THRESHOLD
        )


# ═════════════════════════════════════════════════════════════════════
# Mutation 3 — break the TTL comparison
# ═════════════════════════════════════════════════════════════════════


class TestMutation3BrokenTtl:
    async def test_the_expired_cell_is_green_without_the_mutation(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        live = flow.token_repo.find_live(flow.version_id)
        with Session(flow.engine) as session:
            stored = session.get(PromptAssentToken, live.token_id)
            stored.ttl_expires_at = "2020-01-01T00:00:00Z"
            session.add(stored)
            session.commit()
        echo(flow, token)
        result = await decide(flow, token=token)
        assert "reason=token-expired" in result, result
        assert flow.repo.get_version(flow.version_id).state == "pending_approval"

    async def test_breaking_the_ttl_comparison_reddens_the_cell(
        self, monkeypatch
    ) -> None:
        """Mutation: ``is_expired`` always answers False ⇒ the expired
        token APPROVES ⇒ the cell's pin REDS."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        monkeypatch.setattr(
            PromptAssentTokenRepository,
            "is_expired",
            lambda self, row_or_expires_at: False,
        )
        flow = build_flow()
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        live = flow.token_repo.find_live(flow.version_id)
        with Session(flow.engine) as session:
            stored = session.get(PromptAssentToken, live.token_id)
            stored.ttl_expires_at = "2020-01-01T00:00:00Z"
            session.add(stored)
            session.commit()
        echo(flow, token)
        result = await decide(flow, token=token)
        with pytest.raises(AssertionError) as reddened:
            assert "reason=token-expired" in result, result
            assert (
                flow.repo.get_version(flow.version_id).state
                == "pending_approval"
            )
        # Evidence: the long-expired token was ACCEPTED.
        assert flow.repo.get_version(flow.version_id).state == "approved"


# ═════════════════════════════════════════════════════════════════════
# Mutation 4 — drop the state predicate from the CAS
# ═════════════════════════════════════════════════════════════════════


class TestMutation4DroppedCasPredicate:
    async def test_the_non_pending_cell_is_green_without_the_mutation(
        self, monkeypatch, sqlite_engine, pv_repo, pending_row  # noqa: F811
    ) -> None:
        """At the REPO layer: the CAS refuses a second approval of a
        decided row — nothing is written."""
        from daemon.repositories.prompt_version.repository import (
            ActorContext,
        )

        human = ActorContext(subject="op", auth_method="session")
        submitter = actor_from_instance("inst-1", "ari")
        first = pv_repo.approve(pending_row.version_id, human)
        assert first.state == "approved"
        with pytest.raises(Exception):
            pv_repo.approve(pending_row.version_id, human)
        with Session(sqlite_engine) as session:
            rows = session.exec(
                select(PromptAuditEvent).where(
                    col(PromptAuditEvent.version_id) == pending_row.version_id,
                    col(PromptAuditEvent.event_type) == "APPROVED",
                )
            ).all()
        assert len(rows) == 1, "exactly one decision row"

    async def test_dropping_the_state_predicate_reddens_the_cell(
        self, monkeypatch, sqlite_engine, pv_repo, pending_row  # noqa: F811
    ) -> None:
        """Mutation: the CAS UPDATE loses its ``state == from_state``
        predicate (a forked atomic_transition) ⇒ a decided row is
        re-approved and gains a SECOND decision audit row ⇒ the cell's
        pins (untouched row; no extra decision event) RED."""
        from daemon.repositories.prompt_version.repository import (
            ActorContext,
        )

        def _transition_without_state_predicate(
            self, version_id, from_state, to_state, actor, **kwargs
        ):
            """A FORK of ``atomic_transition`` with the CAS predicate
            dropped — the mutation under test (never production code).
            Only the ``state == from_state`` WHERE leg is removed;
            everything else mirrors the real CAS."""
            from daemon.repositories.prompt_version.models import (
                PromptVersion,
            )
            from daemon.repositories.prompt_version.repository import (
                _now_iso,
            )

            with self.engine.begin() as conn:
                upd = (
                    PromptVersion.__table__.update()
                    .where(col(PromptVersion.version_id) == version_id)
                    .values(
                        state=to_state,
                        updated_at=_now_iso(),
                        approver_subject=kwargs.get("approver_subject"),
                        approver_auth_method=kwargs.get(
                            "approver_auth_method"
                        ),
                    )
                )
                conn.execute(upd)
                self._append_audit_in_conn(
                    conn,
                    agent_id=self._get_field_in_conn(conn, version_id, "agent_id"),
                    version_tag=self._get_field_in_conn(
                        conn, version_id, "version_tag"
                    ),
                    artifact=self._get_field_in_conn(conn, version_id, "artifact"),
                    version_id=version_id,
                    event_type="APPROVED",
                    actor=actor,
                    payload={
                        "from_state": from_state,
                        "to_state": to_state,
                        "approver_subject": kwargs.get("approver_subject"),
                        "approver_auth_method": kwargs.get(
                            "approver_auth_method"
                        ),
                    },
                )

        human = ActorContext(subject="op", auth_method="session")
        pv_repo.approve(pending_row.version_id, human)
        with Session(sqlite_engine) as session:
            before = session.exec(
                select(PromptAuditEvent).where(
                    col(PromptAuditEvent.version_id) == pending_row.version_id,
                    col(PromptAuditEvent.event_type) == "APPROVED",
                )
            ).all()
        assert len(before) == 1

        monkeypatch.setattr(
            PromptVersionRepository,
            "approve",
            lambda self, vid, actor: _transition_without_state_predicate(
                self, vid, "pending_approval", "approved", actor,
                approver_subject=actor.subject,
                approver_auth_method=actor.auth_method,
            ),
        )
        pv_repo.approve(pending_row.version_id, human)  # the loser "wins"
        with pytest.raises(AssertionError) as reddened:
            with Session(sqlite_engine) as session:
                after = session.exec(
                    select(PromptAuditEvent).where(
                        col(PromptAuditEvent.version_id)
                        == pending_row.version_id,
                        col(PromptAuditEvent.event_type) == "APPROVED",
                    )
                ).all()
            assert len(after) == 1, "exactly one decision row"
        assert len(after) == 2, (
            "mutation evidence: the predicate-dropped CAS wrote a SECOND "
            "decision row on a decided item"
        )


# ═════════════════════════════════════════════════════════════════════
# Mutation 5 — a non-user-origin dispatch leaves the window alive
# ═════════════════════════════════════════════════════════════════════


class _Win:
    """The manager window surface, minimal (the stamp method reads three
    attributes)."""

    _user_origin_windows = {}
    _user_origin_last_stamp = {}
    source_registry = None


class TestMutation5WindowLeftAlive:
    def test_the_construction_property_is_green_without_the_mutation(self):
        _Win._user_origin_windows = {}
        _Win._user_origin_last_stamp = {}
        _Win.source_registry = _fake_registry()
        InstanceManager.stamp_user_origin_window(
            _Win, "inst-x", "discord", "msg-1"
        )
        assert "inst-x" in _Win._user_origin_windows
        InstanceManager.stamp_user_origin_window(
            _Win, "inst-x", "scheduler", "msg-2"
        )
        assert "inst-x" not in _Win._user_origin_windows, (
            "an agent-originated dispatch MUST pop the window"
        )

    def test_leaving_the_window_alive_reddens(self, monkeypatch):
        """Mutation: the non-user-origin branch SKIPS the pop ⇒ the
        agent-originated turn inherits the earlier user turn's
        authorization ⇒ the construction property REDS."""

        def _mutated_stamp(self, instance_id, source, message_id):
            from daemon.tools import upgrade_journal as _uj

            registry_get = (
                self.source_registry.get if self.source_registry else None
            )
            is_user, detail = _uj.classify_user_origin(source, registry_get)
            now = _uj.now_iso()
            self._user_origin_last_stamp[instance_id] = {
                "source": source, "message_id": message_id,
                "stamped": is_user, "detail": detail, "stamped_at": now,
            }
            if is_user:
                self._user_origin_windows[instance_id] = {
                    "source": source,
                    "message_id": message_id,
                    "stamped_at": now,
                    "expires_at": _uj.iso_plus(now, 3600),
                }
            # MUTATION: the else-branch (the pop) is GONE.

        monkeypatch.setattr(
            InstanceManager, "stamp_user_origin_window", _mutated_stamp
        )
        _Win._user_origin_windows = {}
        _Win._user_origin_last_stamp = {}
        _Win.source_registry = _fake_registry()
        _mutated_stamp(_Win, "inst-x", "discord", "msg-1")
        _mutated_stamp(_Win, "inst-x", "scheduler", "msg-2")
        with pytest.raises(AssertionError) as reddened:
            assert "inst-x" not in _Win._user_origin_windows
        assert "inst-x" in _Win._user_origin_windows, (
            "mutation evidence: the window SURVIVED the agent dispatch"
        )


# ═════════════════════════════════════════════════════════════════════
# Mutation 6 — fork / re-derive the classifier
# ═════════════════════════════════════════════════════════════════════


class TestMutation6ForkedClassifier:
    def test_the_unforked_reuse_pin_holds(self):
        """The pin: the tool consults the UNFORKED classifier — it
        imports the function and never defines its own."""
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[2]
            / "daemon/tools/prompt_assent.py"
        ).read_text(encoding="utf-8")
        assert "from daemon.tools.upgrade_journal import classify_user_origin" in source
        assert not re.search(r"def\s+classify_user_origin", source), (
            "a forked classifier in the tool module is the defect this "
            "pin exists to catch"
        )
        gate = source.split("def _classify_window_source", 1)[1].split(
            "\ndef ", 1
        )[0]
        assert "classify_user_origin(source, registry_get)" in gate

    async def test_forking_the_classifier_reddens_the_cell(
        self, monkeypatch
    ) -> None:
        """Mutation: a FORKED classifier (any source whose id contains
        'discord' is user-origin — registry metadata ignored) ⇒ a
        scheduler-sourced window is ACCEPTED ⇒ the non-user-origin
        cell's refusal pin REDS."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")

        def _forked_classify(manager, source):
            # The FORK: a re-derivation that admits ANY non-empty
            # source — divergent from the UNFORKED trust anchor.
            return (isinstance(source, str) and bool(source), "forked:any-string")

        monkeypatch.setattr(
            "daemon.tools.prompt_assent._classify_window_source",
            _forked_classify,
        )
        flow = build_flow(window=build_window(source="scheduler"))
        result = await decide(flow, token=UNKNOWN_TOKEN)
        with pytest.raises(AssertionError) as reddened:
            assert "reason=no-user-origin-turn" in result, result
        assert "reason=no-user-origin-turn" not in result, (
            "mutation evidence: the forked classifier ADMITTED the "
            f"scheduler-sourced turn ({result.splitlines()[0]})"
        )


# ═════════════════════════════════════════════════════════════════════
# The column-source pin mutation (K-7) — feed the column the WRONG source
# ═════════════════════════════════════════════════════════════════════


class TestMutationColumnSource:
    def test_the_mint_pin_is_green_without_the_mutation(self, sqlite_engine, pv_repo, pending_row):  # noqa: F811
        from daemon.tools.prompt_assent import token_hash as _th

        flow_manager = None
        flow = build_flow(engine=sqlite_engine)
        # Direct mint (the P2 mint path) with the CORRECT render hash.
        from daemon.services.prompt_version_diff import render_sha256

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
        row = flow.token_repo.mint(
            version_id=pending_row.version_id,
            agent_id=pending_row.agent_id,
            version_tag=pending_row.version_tag,
            artifact=pending_row.artifact,
            version=pending_row.version,
            token_hash=hashlib.sha256(b"CONFIRM-COLSRC01").hexdigest(),
            render_sha256=mint_sha,
        )
        content_hash = hashlib.sha256(
            pending_row.content.encode("utf-8")
        ).hexdigest()
        assert row.render_sha256 == mint_sha
        assert row.render_sha256 != content_hash, (
            "the column must NOT be the prompt_versions.content_hash copy"
        )

    def test_feeding_the_column_from_content_hash_reddens(
        self, monkeypatch, sqlite_engine, pv_repo, pending_row  # noqa: F811
    ):
        """Mutation: the mint feeds ``render_sha256`` from
        ``sha256(pending content)`` — the K-7 FORBIDDEN source ⇒ the
        column-source pin REDS."""
        flow = build_flow(engine=sqlite_engine)

        real_mint = PromptAssentTokenRepository.mint

        def _content_hash_mint(self, **kwargs):
            kwargs["render_sha256"] = hashlib.sha256(
                pending_row.content.encode("utf-8")
            ).hexdigest()
            return real_mint(self, **kwargs)

        monkeypatch.setattr(
            PromptAssentTokenRepository, "mint", _content_hash_mint
        )
        from daemon.services.prompt_version_diff import render_sha256

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
        row = flow.token_repo.mint(
            version_id=pending_row.version_id,
            agent_id=pending_row.agent_id,
            version_tag=pending_row.version_tag,
            artifact=pending_row.artifact,
            version=pending_row.version,
            token_hash=hashlib.sha256(b"CONFIRM-COLSRC02").hexdigest(),
            render_sha256=mint_sha,
        )
        with pytest.raises(AssertionError) as reddened:
            assert row.render_sha256 == mint_sha
            assert row.render_sha256 != hashlib.sha256(
                pending_row.content.encode("utf-8")
            ).hexdigest()
        assert row.render_sha256 == hashlib.sha256(
            pending_row.content.encode("utf-8")
        ).hexdigest(), (
            "mutation evidence: the column now carries the FORBIDDEN "
            "content-hash copy"
        )


def _fake_registry():
    from tests.helpers.prompt_assent_tool_fixture import FakeSourceRegistry

    return FakeSourceRegistry()
