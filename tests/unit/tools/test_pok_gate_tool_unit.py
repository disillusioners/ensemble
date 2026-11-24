"""POK write-gate TOOL pack (Wave 2 Phase 8) — the authority split, the
ASK round-trip, the G1 answer tool, and the fork pins.

Legs: the gate NEVER writes and the TOOL executes; the audit records
BOTH the verdict and the execution (two rows for a plain chain, three
for the ASK chain); the "confirmation required" payload; the G1 answer
path (confirm / decline / the six typed refusals — a missing or
unclassifiable answer NEVER defaults to accept); the MERGE legs (the
no-delta merge writes NO row and stamps the target's verify_count; the
delta merge asks under the default policy and executes on
confirmation); the tier pre-check (a pinned target is routed to the
curator, never auto-merged); the R-RET rename (the write lands on the
proposed slug); the curate-path archive basis (LEDGERED); the
credential-shape scan (the raw value never reaches the ledger); the
two sibling switches (both OFF at build); the ``pok_gate_answer``
registration (KNOWN_TOOL_NAMES 269 → 270); the gate slot (a no-op
short-circuits BEFORE the gate; a supersede IS judged).

The judge is REPLACED by a deterministic stub in every exercise — no
LLM, no network, no live store (a scratch in-memory SQLite engine).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import daemon.constants as constants
import daemon.repositories.pok.models  # noqa: F401
from daemon.repositories.pok.models import (
    POKWriteGateLedger,
    ProjectOperatingKnowledge,
    ProjectOperatingKnowledgeEmbedding,
)
from daemon.repositories.pok.repository import POKRepository
from daemon.services.pok_gate import GateVerdict
from daemon.tools import pok as pok_tools
from daemon.tools.pok import create_pok_tools


@pytest.fixture()
def tool_env(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            ProjectOperatingKnowledge.__table__,
            ProjectOperatingKnowledgeEmbedding.__table__,
            POKWriteGateLedger.__table__,
        ],
    )

    class FakeProjectRepo:
        def get(self, pid):
            if pid == "default-x":
                return SimpleNamespace(name="__system_default__")
            return SimpleNamespace(name="proj-one")

    def make_repo(project_id, agent_id):
        class FakeRepo:
            def get(self, iid):
                return SimpleNamespace(project_id=project_id, agent_id=agent_id)

        return FakeRepo()

    saved = constants.SYSTEM_DEFAULT_PROJECT_ID
    state = SimpleNamespace(
        engine=engine,
        make_repo=make_repo,
        project_repo=FakeProjectRepo(),
        saved_default=saved,
    )
    yield state
    constants.SYSTEM_DEFAULT_PROJECT_ID = saved


def _manager(tool_env, project_id, agent_id):
    return SimpleNamespace(
        _engine=tool_env.engine,
        _instance_repository=tool_env.make_repo(project_id, agent_id),
        _project_repository=tool_env.project_repo,
    )


def _tools(tool_env, project_id="p1", agent_id="developer"):
    return create_pok_tools(
        _manager(tool_env, project_id, agent_id),
        current_instance_id="inst-1",
        agent_id=agent_id,
    )


def _install_judge(monkeypatch, verdict: GateVerdict, calls: list | None = None):
    """Replace the judge with a deterministic verdict (no LLM)."""

    def _fake(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        return verdict

    monkeypatch.setattr(pok_tools, "judge_pok_write", _fake)


def _enforce(monkeypatch, on: bool = True):
    monkeypatch.setattr(
        pok_tools, "read_gate_switches", lambda engine: (on, False)
    )


def _ledger(tool_env, project_id="p1"):
    return POKRepository(tool_env.engine).query_ledger(project_id, limit=None)


# ── the authority split (T8.1 / T8.2 / T8.3) ──────────────────────────────


class TestAuthoritySplit:
    def test_the_gate_writes_nothing_and_the_tool_executes(self, tool_env, monkeypatch) -> None:
        """T8.1 — the gate is a pure verdict function; the TOOL writes."""
        _enforce(monkeypatch)
        _install_judge(
            monkeypatch, GateVerdict(verdict="ACCEPT", family="scope", gate_id="g-1")
        )
        tools = _tools(tool_env)
        out = tools[0].invoke(
            {"slug": "fact-01", "body": "the deploy gate runs first", "reason": "the invariant"}
        )
        assert out["action"] == "created"
        # ONE knowledge row exists — the tool wrote it, not the gate.
        rows = POKRepository(tool_env.engine).query_ledger("p1", limit=None)
        assert len(rows) == 2  # the ADMISSION verdict + the EXECUTION
        assert {r.phase for r in rows} == {"ADMISSION", "EXECUTION"}
        assert all(r.gate_id == "g-1" for r in rows)

    def test_the_execution_row_rides_the_write_transaction(self, tool_env, monkeypatch) -> None:
        """§13.10 — the EXECUTION row is written with the write."""
        _enforce(monkeypatch)
        _install_judge(
            monkeypatch, GateVerdict(verdict="ACCEPT", family="scope", gate_id="g-2")
        )
        tools = _tools(tool_env)
        tools[0].invoke(
            {"slug": "fact-02", "body": "the deploy gate runs first", "reason": "the invariant"}
        )
        rows = _ledger(tool_env)
        execution = [r for r in rows if r.phase == "EXECUTION"]
        assert len(execution) == 1
        assert execution[0].executed is True
        assert execution[0].entry_id

    def test_a_no_op_short_circuits_before_the_gate(self, tool_env, monkeypatch) -> None:
        """D5 — the exact-dup no-op NEVER reaches the gate."""
        _enforce(monkeypatch)
        calls: list = []
        _install_judge(
            monkeypatch, GateVerdict(verdict="ACCEPT", family="scope", gate_id="g-3"), calls
        )
        tools = _tools(tool_env)
        first = tools[0].invoke(
            {"slug": "fact-03", "body": "the same body", "reason": "the invariant"}
        )
        assert first["action"] == "created"
        second = tools[0].invoke(
            {"slug": "fact-03", "body": "the same body", "reason": "the invariant"}
        )
        assert second["action"] == "no-op"
        assert len(calls) == 1  # the no-op did NOT call the gate

    def test_a_supersede_is_judged(self, tool_env, monkeypatch) -> None:
        """T8.9 — a supersede IS judged (the digest carries the prior)."""
        _enforce(monkeypatch)
        calls: list = []
        _install_judge(
            monkeypatch, GateVerdict(verdict="ACCEPT", family="scope", gate_id="g-4"), calls
        )
        tools = _tools(tool_env)
        tools[0].invoke({"slug": "fact-04", "body": "v1", "reason": "the invariant"})
        tools[0].invoke({"slug": "fact-04", "body": "v2 differs", "reason": "the invariant"})
        assert len(calls) == 2


# ── the ASK round-trip (T8.4 / T8.7) ───────────────────────────────────────


class TestAskRoundTrip:
    def test_an_ask_returns_a_confirmation_required_payload_and_writes_nothing(
        self, tool_env, monkeypatch
    ) -> None:
        _enforce(monkeypatch)
        _install_judge(
            monkeypatch,
            GateVerdict(
                verdict="ASK",
                family="reason",
                gate_id="g-ask",
                question="which invariant does this protect?",
            ),
        )
        tools = _tools(tool_env)
        out = tools[0].invoke(
            {"slug": "fact-ask", "body": "the body", "reason": "the invariant"}
        )
        assert out["action"] == "confirmation_required"
        assert out["gate_id"] == "g-ask"
        assert out["question"]
        # NOTHING was written.
        rows = POKRepository(tool_env.engine).query_ledger("p1", limit=None)
        entry_rows = [r for r in rows if r.phase == "EXECUTION"]
        assert entry_rows == []
        # The ASK_MINTED row IS there.
        assert [r.phase for r in rows] == ["ASK_MINTED"]

    def test_a_confirming_answer_executes_the_write(self, tool_env, monkeypatch) -> None:
        _enforce(monkeypatch)
        _install_judge(
            monkeypatch,
            GateVerdict(
                verdict="ASK",
                family="reason",
                gate_id="g-ask2",
                question="which invariant?",
            ),
        )
        tools = _tools(tool_env)
        tools[0].invoke({"slug": "fact-ask2", "body": "the body", "reason": "the invariant"})
        answer = tools[3].invoke({"gate_id": "g-ask2", "answer": "yes, confirmed"})
        assert answer["written"] is True
        assert answer["slug"] == "fact-ask2"
        rows = _ledger(tool_env)
        phases = [r.phase for r in rows]
        assert "ASK_MINTED" in phases
        assert "ASK_ANSWERED" in phases
        assert "ASK_EXECUTED" in phases

    def test_a_declining_answer_writes_nothing(self, tool_env, monkeypatch) -> None:
        _enforce(monkeypatch)
        _install_judge(
            monkeypatch,
            GateVerdict(
                verdict="ASK",
                family="reason",
                gate_id="g-ask3",
                question="which invariant?",
            ),
        )
        tools = _tools(tool_env)
        tools[0].invoke({"slug": "fact-ask3", "body": "the body", "reason": "the invariant"})
        answer = tools[3].invoke({"gate_id": "g-ask3", "answer": "no, declined"})
        assert answer["written"] is False
        rows = _ledger(tool_env)
        assert "ASK_EXECUTED" not in [r.phase for r in rows]

    def test_an_unclassifiable_answer_never_defaults_to_accept(
        self, tool_env, monkeypatch
    ) -> None:
        """T8.7 / the Emperor's catch — a garbage answer writes nothing."""
        _enforce(monkeypatch)
        _install_judge(
            monkeypatch,
            GateVerdict(
                verdict="ASK",
                family="reason",
                gate_id="g-ask4",
                question="which invariant?",
            ),
        )
        tools = _tools(tool_env)
        tools[0].invoke({"slug": "fact-ask4", "body": "the body", "reason": "the invariant"})
        answer = tools[3].invoke({"gate_id": "g-ask4", "answer": "hmm, maybe later"})
        assert answer["code"] == "pok_gate_answer_unrecognized"
        rows = _ledger(tool_env)
        assert "ASK_EXECUTED" not in [r.phase for r in rows]

    def test_a_missing_answer_is_refused(self, tool_env, monkeypatch) -> None:
        _enforce(monkeypatch)
        _install_judge(
            monkeypatch,
            GateVerdict(
                verdict="ASK",
                family="reason",
                gate_id="g-ask5",
                question="which invariant?",
            ),
        )
        tools = _tools(tool_env)
        tools[0].invoke({"slug": "fact-ask5", "body": "the body", "reason": "the invariant"})
        answer = tools[3].invoke({"gate_id": "g-ask5", "answer": "   "})
        assert answer["code"] == "pok_gate_answer_required"

    def test_an_unknown_gate_is_refused(self, tool_env) -> None:
        tools = _tools(tool_env)
        answer = tools[3].invoke({"gate_id": "nope", "answer": "yes"})
        assert answer["code"] == "pok_gate_unknown"

    def test_a_foreign_agent_cannot_answer(self, tool_env, monkeypatch) -> None:
        _enforce(monkeypatch)
        _install_judge(
            monkeypatch,
            GateVerdict(
                verdict="ASK",
                family="reason",
                gate_id="g-ask6",
                question="which invariant?",
            ),
        )
        tools = _tools(tool_env)
        tools[0].invoke({"slug": "fact-ask6", "body": "the body", "reason": "the invariant"})
        other = create_pok_tools(
            _manager(tool_env, "p1", "leader"),
            current_instance_id="inst-2",
            agent_id="leader",
        )
        answer = other[3].invoke({"gate_id": "g-ask6", "answer": "yes"})
        assert answer["code"] == "pok_gate_not_owner"

    def test_a_second_answer_is_an_audited_no_op(self, tool_env, monkeypatch) -> None:
        _enforce(monkeypatch)
        _install_judge(
            monkeypatch,
            GateVerdict(
                verdict="ASK",
                family="reason",
                gate_id="g-ask7",
                question="which invariant?",
            ),
        )
        tools = _tools(tool_env)
        tools[0].invoke({"slug": "fact-ask7", "body": "the body", "reason": "the invariant"})
        tools[3].invoke({"gate_id": "g-ask7", "answer": "yes"})
        again = tools[3].invoke({"gate_id": "g-ask7", "answer": "yes"})
        assert again["code"] == "pok_gate_already_answered"


# ── the MERGE legs (T7.6 / T8.2 / T8.12 / phase11 T12) ────────────────────


class TestMergeLegs:
    def _seed_target(self, tool_env, slug="target-01", pinned=False, tier="agent"):
        repo = POKRepository(tool_env.engine)
        row = ProjectOperatingKnowledge(
            project_id="p1",
            slug=slug,
            domain="other",
            audience="operation",
            audience_agents=None,
            body="the target row body",
            provenance="observed",
            status="active",
            disputed=False,
            superseded_by_id=None,
            content_sha256="x",
            created_by_agent="developer",
            source_tier=tier,
            pinned=pinned,
        )
        repo.insert(row)
        return row

    def test_a_no_delta_merge_writes_no_row_and_stamps_the_target(
        self, tool_env, monkeypatch
    ) -> None:
        """T7.6 / F2 — a no-delta merge is NEVER a write."""
        _enforce(monkeypatch)
        target = self._seed_target(tool_env)
        _install_judge(
            monkeypatch,
            GateVerdict(
                verdict="MERGE",
                family="redundancy",
                gate_id="g-merge",
                target_slug="target-01",
                merge_kind="no_delta",
            ),
        )
        tools = _tools(tool_env)
        out = tools[0].invoke(
            {"slug": "new-one", "body": "the same fact", "reason": "the invariant"}
        )
        assert out["action"] == "merged"
        assert out["executed"] is False
        # No second row was written.
        rows = POKRepository(tool_env.engine).list_entries("p1", statuses=("active",))
        assert [r.slug for r in rows] == ["target-01"]
        # The target's verify_count was stamped (the first real writer).
        stamped = POKRepository(tool_env.engine).get(target.id)
        assert (stamped.verify_count or 0) >= 1

    def test_a_delta_merge_asks_by_default_and_executes_on_confirmation(
        self, tool_env, monkeypatch
    ) -> None:
        """F2 — the default policy is ASK_WITH_PROPOSAL."""
        _enforce(monkeypatch)
        target = self._seed_target(tool_env)
        _install_judge(
            monkeypatch,
            GateVerdict(
                verdict="MERGE",
                family="redundancy",
                gate_id="g-merge2",
                target_slug="target-01",
                merge_kind="delta",
                proposed_body="the target row body plus the nuance",
                proposed_body_sha256="deadbeef",
            ),
        )
        tools = _tools(tool_env)
        out = tools[0].invoke(
            {"slug": "new-two", "body": "the same fact", "reason": "the invariant"}
        )
        assert out["action"] == "confirmation_required"
        assert out["target_slug"] == "target-01"
        # The FULL target body rides the payload (not the 80-char slice).
        assert out["target_body"] == "the target row body"
        answer = tools[3].invoke({"gate_id": out["gate_id"], "answer": "yes, confirmed"})
        assert answer["written"] is True
        # The merge is COUNT-PRESERVING: one active row, the target's
        # slug, the new body.
        rows = POKRepository(tool_env.engine).list_entries("p1", statuses=("active",))
        assert len(rows) == 1
        assert rows[0].slug == "target-01"
        assert rows[0].body == "the target row body plus the nuance"

    def test_a_pinned_target_is_routed_to_the_curator(self, tool_env, monkeypatch) -> None:
        """T8.12 — an untouchable target is NEVER auto-merged."""
        _enforce(monkeypatch)
        self._seed_target(tool_env, pinned=True)
        _install_judge(
            monkeypatch,
            GateVerdict(
                verdict="MERGE",
                family="redundancy",
                gate_id="g-merge3",
                target_slug="target-01",
                merge_kind="delta",
                proposed_body="the merged body",
            ),
        )
        tools = _tools(tool_env)
        out = tools[0].invoke(
            {"slug": "new-three", "body": "the same fact", "reason": "the invariant"}
        )
        assert out["action"] == "confirmation_required"
        assert out["routed_to"] == "curator"
        assert "curator" in out["question"]


# ── the R-RET rename (T8.15) ───────────────────────────────────────────────


class TestRetrievabilityRename:
    def test_the_rename_lands_on_the_proposed_slug(self, tool_env, monkeypatch) -> None:
        _enforce(monkeypatch)
        _install_judge(
            monkeypatch,
            GateVerdict(
                verdict="ASK",
                family="boundary",
                gate_id="g-ret",
                question="re-slug it",
                proposed_slug="deploy-01-smoke-gate",
                retrievability_flagged=True,
                rule_applied="R-RET",
            ),
        )
        tools = _tools(tool_env)
        out = tools[0].invoke(
            {
                "slug": "zzz-99-placeholder",
                "body": "the deploy pipeline must run the smoke gate",
                "reason": "the invariant",
            }
        )
        assert out["action"] == "confirmation_required"
        assert out["proposed_slug"] == "deploy-01-smoke-gate"
        answer = tools[3].invoke({"gate_id": out["gate_id"], "answer": "yes"})
        assert answer["written"] is True
        assert answer["slug"] == "deploy-01-smoke-gate"
        rows = POKRepository(tool_env.engine).list_entries("p1", statuses=("active",))
        assert [r.slug for r in rows] == ["deploy-01-smoke-gate"]


# ── the shadow posture (T8.8) ──────────────────────────────────────────────


class TestShadowPosture:
    def test_in_shadow_the_judge_never_refuses_a_write(self, tool_env, monkeypatch) -> None:
        _enforce(monkeypatch, on=False)
        _install_judge(
            monkeypatch,
            GateVerdict(
                verdict="REJECT", family="boundary", gate_id="g-shadow", advisory=True
            ),
        )
        tools = _tools(tool_env)
        out = tools[0].invoke(
            {"slug": "fact-shadow", "body": "the body", "reason": "the invariant"}
        )
        assert out["action"] == "created"

    def test_in_shadow_an_ask_is_advisory_only(self, tool_env, monkeypatch) -> None:
        """T8.8 — the shadow ASK mints NO pending state and returns NO
        "confirmation required" payload."""
        _enforce(monkeypatch, on=False)
        _install_judge(
            monkeypatch,
            GateVerdict(
                verdict="ASK",
                family="reason",
                gate_id="g-shadow2",
                question="which invariant?",
                advisory=True,
            ),
        )
        tools = _tools(tool_env)
        out = tools[0].invoke(
            {"slug": "fact-shadow2", "body": "the body", "reason": "the invariant"}
        )
        assert out["action"] == "created"
        rows = _ledger(tool_env)
        phases = [r.phase for r in rows]
        assert "ASK_SHADOW" in phases
        assert "ASK_MINTED" not in phases


# ── the mode-match replay (F2) ─────────────────────────────────────────────


class TestModeMatchReplay:
    """F2 — a cached verdict replays ONLY in the mode it was judged in.

    A shadow-era entry replayed after the enforcement switch flips ON
    would fail OPEN (an advisory verdict refuses nothing); an
    enforcement-era entry replayed under shadow would gate a write the
    operator switched to advisory. Either mismatch ⇒ RE-JUDGE.
    """

    def _install_llm(self, monkeypatch) -> list:
        """Replace the house LLM seam with a counting fake."""
        from daemon.services import pok_gate as gate_mod

        calls: list = []

        def _fake(llm_config, prompt, **kwargs):
            calls.append(prompt)
            return json.dumps(
                {
                    "verdict": "ACCEPT",
                    "family": "scope",
                    "explanation": "ok",
                    "alternative": None,
                    "confidence": 0.9,
                }
            )

        monkeypatch.setattr(gate_mod, "_house_llm_call", _fake)
        return calls

    def test_the_next_upsert_re_judges_after_the_switch_flips(
        self, tool_env, monkeypatch
    ) -> None:
        """The dispatch's acceptance: cache under shadow, flip the
        switch ON in-process, and prove the next upsert RE-JUDGES."""
        calls = self._install_llm(monkeypatch)
        pok_tools._GATE_CACHE.clear()
        _enforce(monkeypatch, on=False)  # SHADOW
        tools = _tools(tool_env)
        first = tools[0].invoke(
            {
                "slug": "deploy-01-gate",
                "body": "the deploy gate runs first",
                "reason": "the invariant",
            }
        )
        assert first["action"] == "created"
        assert len(calls) == 1

        # The active row is retired so the SAME (slug, body, reason)
        # write is no longer a no-op — it reaches the gate again, with
        # the SAME cache key (project + slug + body hash + reason hash).
        curator = _tools(tool_env, agent_id="leader")
        archived = curator[2].invoke(
            {"slug": "deploy-01-gate", "archived": True, "basis": "NOW-FALSE"}
        )
        assert archived["action"] == "curated"

        # The enforcement switch flips ON (in-process).
        _enforce(monkeypatch, on=True)
        second = tools[0].invoke(
            {
                "slug": "deploy-01-gate",
                "body": "the deploy gate runs first",
                "reason": "the invariant",
            }
        )
        assert second["action"] == "created"
        assert len(calls) == 2, (
            "the shadow-era verdict replayed after the enforcement flip — "
            "the gate fails OPEN (D2 is fail-closed)"
        )

    def test_a_same_mode_upsert_still_replays(self, tool_env, monkeypatch) -> None:
        """The mode-match must not break the replay it protects."""
        calls = self._install_llm(monkeypatch)
        pok_tools._GATE_CACHE.clear()
        _enforce(monkeypatch, on=False)  # SHADOW
        tools = _tools(tool_env)
        tools[0].invoke(
            {
                "slug": "deploy-02-gate",
                "body": "the deploy gate runs first",
                "reason": "the invariant",
            }
        )
        curator = _tools(tool_env, agent_id="leader")
        curator[2].invoke(
            {"slug": "deploy-02-gate", "archived": True, "basis": "NOW-FALSE"}
        )
        tools[0].invoke(
            {
                "slug": "deploy-02-gate",
                "body": "the deploy gate runs first",
                "reason": "the invariant",
            }
        )
        assert len(calls) == 1, "the same-mode entry did not replay"


# ── the D2 fail-closed pin (phase11 T6) ────────────────────────────────────


class TestFailClosed:
    def test_an_unavailable_judge_writes_nothing_in_enforce_mode(
        self, tool_env, monkeypatch
    ) -> None:
        """D2 — an unavailable judge is a fail-closed refusal, NEVER an
        accidental accept."""
        from daemon.services.pok_gate import POKGateUnavailable

        _enforce(monkeypatch, on=True)

        def _boom(**kwargs):
            raise POKGateUnavailable("timeout")

        monkeypatch.setattr(pok_tools, "judge_pok_write", _boom)
        tools = _tools(tool_env)
        out = tools[0].invoke(
            {"slug": "fact-fail", "body": "the body", "reason": "the invariant"}
        )
        assert out["code"] == "pok_gate_unavailable"
        assert out["reason"] == "timeout"
        rows = POKRepository(tool_env.engine).list_entries("p1", statuses=("active",))
        assert rows == []

    def test_in_shadow_an_unavailable_judge_does_not_block_the_write(
        self, tool_env, monkeypatch
    ) -> None:
        from daemon.services.pok_gate import POKGateUnavailable

        _enforce(monkeypatch, on=False)

        def _boom(**kwargs):
            raise POKGateUnavailable("timeout")

        monkeypatch.setattr(pok_tools, "judge_pok_write", _boom)
        tools = _tools(tool_env)
        out = tools[0].invoke(
            {"slug": "fact-fail2", "body": "the body", "reason": "the invariant"}
        )
        assert out["action"] == "created"


# ── the curate-path ledger (T8.17) ─────────────────────────────────────────


class TestCurateLedger:
    def test_an_archive_emits_a_ledger_row_carrying_the_basis(self, tool_env) -> None:
        tools = _tools(tool_env, agent_id="leader")
        tools[0].invoke(
            {"slug": "fact-archive", "body": "the body", "reason": "the invariant"}
        )
        out = tools[2].invoke(
            {"slug": "fact-archive", "archived": True, "basis": "NOW-FALSE"}
        )
        assert out["action"] == "curated"
        rows = _ledger(tool_env)
        basis_rows = [r for r in rows if r.basis == "NOW-FALSE"]
        assert len(basis_rows) == 1
        assert basis_rows[0].candidate_slug == "fact-archive"


# ── the credential-shape scan (T8.18) ──────────────────────────────────────


class TestCredentialScan:
    def test_a_credential_shaped_reason_is_never_stored_raw(self, tool_env, monkeypatch) -> None:
        _enforce(monkeypatch)
        _install_judge(
            monkeypatch, GateVerdict(verdict="ACCEPT", family="scope", gate_id="g-cred")
        )
        tools = _tools(tool_env)
        tools[0].invoke(
            {
                "slug": "fact-cred",
                "body": "the body",
                "reason": "the key is api_key=abcdefgh12345678 in the env",
            }
        )
        rows = _ledger(tool_env)
        assert rows
        for row in rows:
            assert "abcdefgh12345678" not in (row.reason or "")
            assert "abcdefgh12345678" not in (row.judge_explanation or "")


# ── the switch pins (T8.11 / phase11 T17) ─────────────────────────────────


class TestSwitchPins:
    def test_both_sibling_switches_default_off(self) -> None:
        from daemon.services.settings_service import MANAGED_KEYS

        semantic = MANAGED_KEYS["pok_gate.semantic_enforcement"]
        tightening = MANAGED_KEYS["pok_gate.softmax_tightening"]
        assert semantic.default is False
        assert tightening.default is False
        # DB-backed, NOT env: an env flip is a live config change.
        assert semantic.config_path is None
        assert tightening.config_path is None
        assert semantic.env_var == ""
        assert tightening.env_var == ""

    def test_the_merge_policy_defaults_to_ask_with_proposal(self) -> None:
        from daemon.services.settings_service import MANAGED_KEYS

        spec = MANAGED_KEYS["pok_gate.merge_policy"]
        assert spec.default == "ASK_WITH_PROPOSAL"
        assert set(spec.allowed_values) == {
            "ASK_WITH_PROPOSAL",
            "AUTO",
            "ASK_NO_PROPOSAL",
        }

    def test_the_alias_is_the_literal_purpose_id(self) -> None:
        from daemon.services.pok_gate import POK_GATE_DEFAULT_MODEL

        assert POK_GATE_DEFAULT_MODEL == "quick"


# ── the tool-registration pin (phase11 T11) ────────────────────────────────


class TestToolRegistration:
    def test_pok_gate_answer_is_in_known_tool_names(self) -> None:
        from daemon.tools._tool_registry import KNOWN_TOOL_NAMES

        assert "pok_gate_answer" in KNOWN_TOOL_NAMES
        assert len(KNOWN_TOOL_NAMES) == 270

    def test_the_registry_has_no_drift(self) -> None:
        from daemon.tools._tool_registry import (
            KNOWN_TOOL_NAMES,
            discover_source_only_tool_names,
        )

        source = discover_source_only_tool_names()
        assert source == set(KNOWN_TOOL_NAMES)

    def test_the_answer_tool_is_built_by_the_factory(self, tool_env) -> None:
        tools = _tools(tool_env)
        assert tools[3].name == "pok_gate_answer"
        assert "gate_id" in (tools[3].args_schema.model_fields)


# ── the fork pins: the cache and the answer shape ─────────────────────────


class TestForkPins:
    def test_the_cached_ask_replays_as_ask_with_a_fresh_gate_id(self) -> None:
        """T13 — a cached ASK never becomes an accept."""
        from daemon.services.pok_gate import VerdictCache, judge_pok_write

        cache = VerdictCache()
        payload = json.dumps(
            {
                "verdict": "ASK",
                "family": "reason",
                "explanation": "unclear",
                "alternative": None,
                "confidence": 0.9,
                "question": "which invariant?",
            }
        )
        calls = []

        def _llm(prompt):
            calls.append(prompt)
            return payload

        first = judge_pok_write(
            project_id="p1",
            project_name="proj",
            slug="s",
            body="b",
            reason="r",
            rows=[],
            mode="llm",
            is_production=False,
            llm_call=_llm,
            cache=cache,
        )
        second = judge_pok_write(
            project_id="p1",
            project_name="proj",
            slug="s",
            body="b",
            reason="r",
            rows=[],
            mode="llm",
            is_production=False,
            llm_call=_llm,
            cache=cache,
        )
        assert second.verdict == "ASK"
        assert second.gate_id != first.gate_id
        assert len(calls) == 1

    def test_the_upsert_tool_carries_no_answer_field(self, tool_env) -> None:
        """T11 — the G2 shape must not creep back: there is NO
        answer-field branch inside pok_upsert. The answer path is the
        DEDICATED tool."""
        tools = _tools(tool_env)
        fields = set(tools[0].args_schema.model_fields)
        assert "answer" not in fields
        assert "gate_id" not in fields

    def test_the_answer_tool_carries_no_body_field(self, tool_env) -> None:
        """T11 — the G1 shape: the answer carries NO body."""
        tools = _tools(tool_env)
        fields = set(tools[3].args_schema.model_fields)
        assert fields == {"gate_id", "answer"}
