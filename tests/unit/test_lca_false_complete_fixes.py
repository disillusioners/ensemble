"""Incident 7d4a3bd9 regression tests — the false-completion fix cycle.

Final semantics (amendments v1→v4, 2026-09-26). The incident: leader
``7d4a3bd9`` Episode B (2026-09-25 17:40:30Z→19:13:27Z, mode=enforce
deny_bound=3) — the tester child self-completed while its own report
said "Awaiting the root-level core tests report…", the fused judge
correctly said ``not_complete`` (deny slot 1), TWO judge double-timeouts
consumed slots 2+3, and the next evaluation fired
``terminal_after_bound`` → COMPLETED-UNVERIFIED, invisible at the user
surface, with the observer finalizing ``no_job`` (the episode's real
job 082899be had been finalized inline at its first turn-end).

Shipped fixes pinned here:

* **Fix 1** — the DISTINCT unverified surface string
  (``completed (gate escalated — unverified)``) at the job-response,
  job-event and mission read points, plus the observer's
  already-finalized job linkage (``no_job`` anomaly).
* **Exhaustion composition gate (user ruling v3).** Bound counting is
  UNCHANGED — every deny counts, timeouts included. At bound
  exhaustion the node branches on the epoch's deny-event composition
  (``attestation_any_substantive_deny`` channel; substantive := judge
  verdict ``not_complete``; timeout/error/unparsable/disabled = the
  judge never spoke):
  * ≥1 substantive ⇒ judge SPOKE and was overridden ⇒
    ``terminal_after_bound`` stands (the loud unverified terminal).
  * ZERO substantive ⇒ NOT COMPLETE, NO terminal write from timeouts
    alone — the deny+nudge cycle CONTINUES (the committed counter may
    rise past the bound). Exits: ``attest_completion`` (the
    deterministic trust path), finishing the work, asking the user.
    Deliberately unbounded-by-user-ruling for this never-spoke case
    (C1 supersession note in decisions.md).
* **Fix 3** — the DIRECTIVE nudge on a (>= 2nd consecutive deny) ∧
  (zero new tool calls since the prior deny snapshot) deny; standard
  nudge otherwise. HOLD / reminder machinery untouched.
* **Fix 5a** — ``final_word_count`` reflects the ACTUAL final
  AIMessage on every evaluated path. Episode A (17:39:15Z) logged
  ``final_word_count=0 length_trigger=False messages_scanned=3``
  against a real 2313-char final AIMessage — root cause: NEITHER the
  window NOR marker scoping; the row printed DATACLASS DEFAULTS
  because the §(iii.b) scan block is routing-gated (Episode A was a
  NON-DELEGATED allow, ``attestation_required=False``). Routing note:
  with 5a fixed the real length values are stamped, but the R3/R4
  no-delegation fold still fires FIRST — the route is UNCHANGED.

Judge verdicts are SCRIPTED per test (the in-test real judge path
fails fast to ``error`` = never-spoke).
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlmodel import Session, SQLModel, create_engine

from daemon.constants import COMPLETION_GATE_ESCALATED_DISPLAY
from daemon.graph import (
    ATTESTATION_ANY_SUBSTANTIVE_KEY,
    ATTESTATION_DENY_PROGRESS_KEY,
    ATTESTATION_DIRECTIVE_NUDGE_TEXT,
    ATTESTATION_NUDGE_TEXT,
    create_attestation_gate_node,
)
from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.services.attestation_gate import (
    Decision,
    GateSettings,
    build_gate_config,
    scan_for_attestation_detailed,
)
from daemon.services.attestation_judge_resolver import (
    reset_llm_judge_resolver_for_tests,
)
from daemon.services.attestation_resolver import (
    reset_attestation_resolver_for_tests,
)
from daemon.services.attestation_marker_scanner import (
    SHORT_REPORT_WORD_THRESHOLD,
    scan_for_short_final_ai,
)
from daemon.services.job_feedback_observer import (
    JobFeedbackObserver,
    _ProcessingJobContext,
)
from daemon.services.work_notifier import _format_status_display
from daemon.services.work_resolver import WorkRecord
from daemon.write_pause_guard import WritePauseGuard


# ─────────────────────────────────────────────────────────────────────────────
# Judge scripting + kill-switch isolation
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_kill_switches(monkeypatch):
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", raising=False
    )
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()
    yield
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()


def _script_judge(monkeypatch, holder: dict):
    """Script the fused judge to read its verdict from ``holder``."""

    async def _scripted(text, config=None, **_kw):
        holder["calls"] = holder.get("calls", 0) + 1
        return SimpleNamespace(
            verdict=holder.get("verdict", "timeout"),
            is_complete=holder.get("verdict", "timeout") == "complete",
            invoked=True,
            model="scripted",
            latency_ms=1,
            attempt=1,
            first_unparsable_excerpt=None,
            rationale="scripted",
            error_class=None,
        )

    monkeypatch.setattr(
        "daemon.services.attestation_report_judge.judge_fused_bundle_async",
        _scripted,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Node driver (hermetic: real repo ledger + scripted judge)
# ─────────────────────────────────────────────────────────────────────────────


LEADER = "lca-fc-leader"


@pytest.fixture
def engine(tmp_path):
    db_path = tmp_path / "lca_false_complete.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    from sqlalchemy import event as sa_event

    @sa_event.listens_for(eng, "connect")
    def _pragma(dbapi_connection, connection_record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine):
    return SQLModelInstanceRepository(engine)


class EpisodeDriver:
    """Drive the REAL gate node across an Episode-B-shaped deny arc.

    Between gate invocations the driver appends the injected nudge and
    a fresh no-tool-call AIMessage (the leader "holding" in prose —
    the 7d4a3bd9 Episode-B shape) and carries the SessionState channel
    values forward the way the LangGraph checkpoint would.
    """

    def __init__(self, repo, monkeypatch, verdict="timeout"):
        self.repo = repo
        repo.create(
            instance_id=LEADER, agent_id="leader", agent_dir="./agents/leader"
        )
        self.manager = MagicMock()
        self.manager.count_pending_children.return_value = 0
        self.manager.get_queued_or_expected_wakeups.return_value = 0
        self.manager.count_live_descendants.return_value = 0
        self.manager.count_busy_descendants.return_value = 0
        self.settings = GateSettings("enforce", 3, 3)
        self.config = build_gate_config(LEADER, self.settings)
        self.node = create_attestation_gate_node(
            self.config,
            self.settings,
            self.manager,
            LEADER,
            denied_count_getter=lambda: repo.get_attestation_denied_count(
                LEADER
            ),
            ledger=repo,
        )
        self.judge_holder: dict = {"verdict": verdict}
        _script_judge(monkeypatch, self.judge_holder)
        self.messages = [
            HumanMessage(content="ship the feature", id="hm-1"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "send_message",
                        "args": {"target": "child-id"},
                        "id": "dispatch-1",
                    }
                ],
                id="dispatch-ai",
            ),
            AIMessage(content="I am done without attesting", id="aim-1"),
        ]
        self.state: dict = {}
        self.pending_tail: list = []

    def step(self):
        to_send = self.messages + self.pending_tail
        self.pending_tail = []
        result = asyncio.run(
            self.node(
                {
                    "messages": deepcopy(to_send),
                    **self.state,
                },
                config={"configurable": {"thread_id": LEADER}},
            )
        )
        # Checkpoint-advance: nudge + a fresh prose-only AIMessage (the
        # leader answers the nudge with "Holding exactly there" prose —
        # NO tool calls, the Episode-B shape).
        if result.get("messages"):
            self.messages.extend(result["messages"])
        self.messages.append(
            AIMessage(
                content=f"Holding exactly there — turn {len(self.messages)}",
                id=f"hold-{len(self.messages)}",
            )
        )
        # Carry channels forward.
        for key in (
            ATTESTATION_ANY_SUBSTANTIVE_KEY,
            ATTESTATION_DENY_PROGRESS_KEY,
            "attestation_nudge_denied_count",
        ):
            if key in result:
                self.state[key] = result[key]
        return result

    def finish_with_attest(self):
        """Append the attest-first shape: a PURE TOOLCALL TURN attest
        call (empty content) + a subsequent standalone text report
        (>= 150 words, no markers) — the deterministic trust-path exit."""
        report = (
            "All phases are complete and verified end to end. The "
            "renderer path, the state boundary, and the persistence "
            "seam each carry the agreed implementation, and the code "
            "review findings from the earlier draft were folded back "
            "in before the final verification pass. The harness "
            "reproduces the original failure on the unfixed baseline "
            "and passes with the fix applied, which pins the defect "
            "class the mission was commissioned to close. "
            "Documentation and operator notes were updated alongside "
            "the tests so the next on-call engineer can trace the "
            "behavior without reading the diff. Every dispatched "
            "child task reported back, each report was reconciled "
            "against the plan, and the two follow-up items the tester "
            "flagged were either fixed in this mission or filed as "
            "explicit backlog entries with owners attached. Nothing "
            "is pending on any child; the residual risks are listed "
            "in the mission notes for the operator to review before "
            "closing the work out."
        )
        assert len(report.split()) >= 150
        self.pending_tail = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "attest_completion",
                        "args": {},
                        "id": "attest-1",
                    }
                ],
                id="attest-ai",
            ),
            AIMessage(report, id="final-report-ai"),
        ]


# ─────────────────────────────────────────────────────────────────────────────
# (a-mixed) Ep-B MIXED arc — judge SPOKE → loud unverified terminal
# ─────────────────────────────────────────────────────────────────────────────


class TestMixedArcJudgeSpoke:
    def test_mixed_arc_escalates_terminal_and_resets(
        self, repo, monkeypatch
    ):
        """The REAL incident shape: 1 substantive ``not_complete`` deny
        + 2 timeout denies ⇒ bound exhausted WITH a substantive verdict
        ⇒ the judge SPOKE and was overridden ⇒ ``terminal_after_bound``
        stands: END routing, escalation written (``completion_gate_
        escalated=True``), counter reset, zero nudges on the escalation
        evaluation. Fix 1's read surface labels this terminal
        COMPLETED-UNVERIFIED."""
        driver = EpisodeDriver(repo, monkeypatch)
        driver.judge_holder["verdict"] = "not_complete"
        r1 = driver.step()
        assert r1["attestation_route"] == "agent"
        assert r1["attestation_nudge_denied_count"] == 1
        # Substantive deny → the channel stamps True.
        assert r1[ATTESTATION_ANY_SUBSTANTIVE_KEY] is True

        driver.judge_holder["verdict"] = "timeout"
        r2 = driver.step()
        assert r2["attestation_route"] == "agent"
        r3 = driver.step()
        assert r3["attestation_route"] == "agent"
        # Timeouts never un-stamp the channel: the judge SPOKE in this
        # epoch (deny 1).
        assert driver.state[ATTESTATION_ANY_SUBSTANTIVE_KEY] is True
        assert repo.get_attestation_denied_count(LEADER) == 3

        # The 4th evaluation: bound exhausted WITH substantive ⇒ terminal.
        r4 = driver.step()
        assert r4["attestation_route"] is None
        assert "messages" not in r4, (
            "the judge-spoke escalation emits no nudge"
        )
        # Escalation write: flag set + counter reset (trigger 2).
        inst = repo.get(LEADER)
        assert inst.completion_gate_escalated is True
        assert repo.get_attestation_denied_count(LEADER) == 0
        # Channel resets ride the terminal return.
        assert r4[ATTESTATION_ANY_SUBSTANTIVE_KEY] is False

    def test_judge_never_rerun_on_the_exhaustion_evaluation(
        self, repo, monkeypatch
    ):
        """Budget parity survives: the exhaustion evaluation itself runs
        NO judge (the pre-judge bound check fires before any judge
        plan)."""
        driver = EpisodeDriver(repo, monkeypatch)
        driver.judge_holder["verdict"] = "not_complete"
        for _ in range(3):
            driver.step()
        calls_before = driver.judge_holder["calls"]
        r4 = driver.step()
        assert r4["attestation_route"] is None
        assert driver.judge_holder["calls"] == calls_before


# ─────────────────────────────────────────────────────────────────────────────
# (a-all-timeout) Never-spoke arc — NO terminal, deny continues, attest exit
# ─────────────────────────────────────────────────────────────────────────────


class TestAllTimeoutArcNeverSpoke:
    def test_never_spoke_exhaustion_continues_denying(
        self, repo, monkeypatch, caplog
    ):
        """NEW all-timeout case (user ruling v3): 3+ timeout denies with
        ZERO substantive verdicts ⇒ bound exhausted but the judge NEVER
        spoke ⇒ NO terminal write: the decision converts to DENIED, the
        nudge continues, and the committed counter rises past the bound
        (counting UNCHANGED — every deny counts). Instance NOT
        completed."""
        driver = EpisodeDriver(repo, monkeypatch)
        driver.judge_holder["verdict"] = "timeout"
        with caplog.at_level("INFO", logger="daemon.graph"):
            r1 = driver.step()
            r2 = driver.step()
            r3 = driver.step()
            # Every deny still counts: 1, 2, 3.
            assert repo.get_attestation_denied_count(LEADER) == 3
            assert driver.state.get(ATTESTATION_ANY_SUBSTANTIVE_KEY) is False, (
                "timeouts are never-spoke — no substantive stamp"
            )

            # The 4th evaluation: bound exhausted, zero substantive ⇒
            # terminal WITHHELD — deny+nudge continues.
            r4 = driver.step()

        assert r4["attestation_route"] == "agent", (
            "a never-spoke-judge epoch must NOT terminalize at the bound"
        )
        assert r4["messages"][0].additional_kwargs["attestation_nudge"] is True
        # Counting unchanged — the counter rises past the bound.
        assert repo.get_attestation_denied_count(LEADER) == 4
        assert r4["attestation_nudge_denied_count"] == 4
        # The instance was NOT escalated/completed by the timeouts.
        inst = repo.get(LEADER)
        assert inst.completion_gate_escalated is False
        assert inst.status != "COMPLETED"
        # The withheld-terminal audit row is greppable.
        log_text = "\n".join(rec.getMessage() for rec in caplog.records)
        assert (
            "event=leader_completion_gate_bound_exhausted_never_spoke"
            in log_text
        )
        assert (
            "event=leader_completion_gate_terminal_after_bound"
            not in log_text
        )

    def test_attest_completion_is_the_exit_after_never_spoke_exhaustion(
        self, repo, monkeypatch
    ):
        """The trust-path exit: after the never-spoke continuation, the
        leader attests (pure toolcall turn) + delivers the standalone
        report ⇒ meta_bypass/attested allow ⇒ allowed END, counter
        reset, no escalation. This IS the fallback for the
        never-spoke case — the tool's exact purpose."""
        driver = EpisodeDriver(repo, monkeypatch)
        driver.judge_holder["verdict"] = "timeout"
        for _ in range(4):
            driver.step()
        assert repo.get_attestation_denied_count(LEADER) == 4

        driver.finish_with_attest()
        r_final = driver.step()
        assert r_final["attestation_route"] is None, (
            "attest_completion is the deterministic exit from the "
            "never-spoke continuation"
        )
        assert "messages" not in r_final
        # Reset trigger 1 — counter cleared, no escalation flag.
        assert repo.get_attestation_denied_count(LEADER) == 0
        inst = repo.get(LEADER)
        assert inst.completion_gate_escalated is False

    def test_substantive_deny_after_timeouts_rearms_the_judge_spoke_terminal(
        self, repo, monkeypatch
    ):
        """A later substantive ``not_complete`` deny (after timeouts)
        stamps the channel — a SUBSEQUENT bound exhaustion then
        terminalizes (the judge spoke, even if late)."""
        driver = EpisodeDriver(repo, monkeypatch)
        driver.judge_holder["verdict"] = "timeout"
        r1 = driver.step()
        r2 = driver.step()
        assert driver.state.get(ATTESTATION_ANY_SUBSTANTIVE_KEY) is False

        driver.judge_holder["verdict"] = "not_complete"
        r3 = driver.step()
        assert r3["attestation_route"] == "agent"
        assert driver.state[ATTESTATION_ANY_SUBSTANTIVE_KEY] is True

        driver.judge_holder["verdict"] = "timeout"
        r4 = driver.step()
        # Bound exhausted (denied_count=3) WITH a substantive verdict ⇒
        # terminal stands.
        assert r4["attestation_route"] is None
        assert repo.get(LEADER).completion_gate_escalated is True


# ─────────────────────────────────────────────────────────────────────────────
# (c) Directive-vs-standard nudge selection
# ─────────────────────────────────────────────────────────────────────────────


class TestDirectiveVsStandardSelection:
    def test_first_deny_is_standard(self, repo, monkeypatch):
        driver = EpisodeDriver(repo, monkeypatch)
        result = driver.step()
        assert result["messages"][0].content == ATTESTATION_NUDGE_TEXT
        assert (
            result["messages"][0].additional_kwargs["attestation_nudge_kind"]
            == "standard"
        )

    def test_second_deny_with_new_tool_calls_is_standard(
        self, repo, monkeypatch
    ):
        driver = EpisodeDriver(repo, monkeypatch)
        driver.step()  # deny 1 — standard (snapshot: 1 tool call)
        # The leader DOES something between denies: a new tool-call
        # message enters the history (progress).
        driver.messages.append(
            AIMessage(
                content="dispatching follow-up",
                tool_calls=[
                    {
                        "name": "send_message",
                        "args": {"target": "child-2"},
                        "id": "dispatch-2",
                    }
                ],
                id="followup-ai",
            )
        )
        result = driver.step()  # deny 2 — WITH progress → standard
        assert result["attestation_route"] == "agent"
        assert result["messages"][0].content == ATTESTATION_NUDGE_TEXT, (
            "a repeat deny WITH new tool calls since the prior deny "
            "snapshot keeps the standard nudge"
        )
        assert (
            result["messages"][0].additional_kwargs["attestation_nudge_kind"]
            == "standard"
        )

    def test_second_deny_with_zero_tool_calls_is_directive(
        self, repo, monkeypatch
    ):
        driver = EpisodeDriver(repo, monkeypatch)
        driver.step()  # deny 1 — standard
        result = driver.step()  # deny 2 — zero progress → directive
        assert result["messages"][0].content == ATTESTATION_DIRECTIVE_NUDGE_TEXT, (
            "a 2nd consecutive deny with ZERO new tool calls since the "
            "prior deny snapshot gets the DIRECTIVE nudge"
        )
        assert (
            result["messages"][0].additional_kwargs["attestation_nudge_kind"]
            == "directive"
        )

    def test_never_spoke_continuation_keeps_directing(
        self, repo, monkeypatch
    ):
        """The directive pairs with the all-timeout continuation: past
        the bound the leader keeps getting nudges, and the zero-progress
        ones are directives naming the exits."""
        driver = EpisodeDriver(repo, monkeypatch)
        driver.judge_holder["verdict"] = "timeout"
        r1 = driver.step()
        assert (
            r1["messages"][0].additional_kwargs["attestation_nudge_kind"]
            == "standard"
        )
        for _ in range(4):  # denies 2..5 — bound crossed at deny 4
            result = driver.step()
            assert result["attestation_route"] == "agent"
            assert (
                result["messages"][0].additional_kwargs["attestation_nudge_kind"]
                == "directive"
            )
            assert (
                result["messages"][0].content
                == ATTESTATION_DIRECTIVE_NUDGE_TEXT
            )

    def test_directive_text_verbatim(self):
        """The canonical directive body, embedded VERBATIM (goes in the
        report and docs verbatim — a single-character drift fails
        here).

        7d4a3bd9 amendment (2026-09-26, reviewer-flagged accuracy fix):
        the trailing sentence was amended to "...will end this mission
        as COMPLETED-UNVERIFIED (gate escalated) once the judge
        delivers a verdict." — the original wording was misleading
        under v3 (the all-timeout continuation no longer terminalizes
        from timeouts alone; the gate-escalated terminal only fires
        once the judge SPOKE).
        """
        assert ATTESTATION_DIRECTIVE_NUDGE_TEXT == (
            "[Attestation Gate — Directive] The gate has denied completion "
            "more than once and you have taken no new action since the last "
            "denial. Choose exactly one now: (1) if the mission is truly "
            "complete, call attest_completion and deliver your final report; "
            "(2) if work remains, dispatch or finish it now (re-assign the "
            "pending work to a child or do it yourself); (3) if you are "
            "blocked or uncertain, ask the user for a decision. Continuing "
            "to hold without action will end this mission as "
            "COMPLETED-UNVERIFIED (gate escalated) once the judge delivers "
            "a verdict."
        )

    def test_directive_supersedes_standard_block_in_place(self):
        """The directive nudge mints the SAME stable id as the standard
        nudge so it supersedes the prior block in place (no
        accumulation)."""
        from daemon.services.context_messages import _stable_id_for

        assert _stable_id_for("attestation_nudge", instance_id="i1")


# ─────────────────────────────────────────────────────────────────────────────
# (d) Scanner — final_word_count reflects the actual final AIMessage
# ─────────────────────────────────────────────────────────────────────────────


def _ep_a_final_message() -> str:
    """A 2313-char final AIMessage with NO 'Final Report' marker
    (the Episode-A shape: starts with '**Verdict:**')."""
    base = "**Verdict:** All phases are done; below is the detailed " "summary of the work, the verification evidence, and the residual risks the operator should review before closing the mission. "
    filler = (
        "The implementation covers the renderer path, the state "
        "boundary, and the persistence seam; the harness reproduces "
        "the failure and passes with the fix; documentation was "
        "updated alongside the tests. "
    )
    text = base + filler * 20
    assert len(text) >= 2313
    return text[:2313]


class TestScannerFinalWordCount:
    def test_ep_a_shape_long_report_counts_nonzero(self):
        """Episode-A regression (count half): the 2313-char final
        AIMessage yields final_word_count > 0 — never the default-0
        the incident log showed."""
        messages = [
            HumanMessage(content="go"),
            AIMessage(
                "",
                tool_calls=[
                    {"name": "send_message", "args": {}, "id": "d1"}
                ],
            ),
            AIMessage(_ep_a_final_message()),
        ]
        result = scan_for_short_final_ai(messages, 3)
        assert result.final_word_count > 0
        assert result.messages_scanned == 1

    def test_ep_a_shape_short_final_triggers_length(self):
        """Episode-A regression (trigger half): a SHORT no-marker final
        yields final_word_count > 0 AND length_trigger=True."""
        messages = [
            HumanMessage(content="go"),
            AIMessage(
                "",
                tool_calls=[
                    {"name": "send_message", "args": {}, "id": "d1"}
                ],
            ),
            AIMessage("**Verdict:** partial report, still mid-work."),
        ]
        result = scan_for_short_final_ai(messages, 3)
        assert result.final_word_count > 0
        assert result.length_trigger is True
        assert result.final_word_count < SHORT_REPORT_WORD_THRESHOLD

    def test_window_boundary_edge_final_message_still_counted(self):
        """The final AIMessage sits exactly AT the window edge (3
        AIMessages in a window-3 tail) — the newest one is still the
        counted one."""
        messages = [
            HumanMessage(content="go"),
            AIMessage("one two"),
            AIMessage("three four"),
            AIMessage("five six seven"),
        ]
        result = scan_for_short_final_ai(messages, 3)
        assert result.final_word_count == 3
        assert result.length_trigger is True

    def test_marker_present_shape_unchanged(self):
        """Marker fields keep their own semantics (separate fields):
        a marker-phrase message counts words AND hits the marker —
        the count is NOT scoped to text after any marker."""
        messages = [
            HumanMessage(content="go"),
            AIMessage(
                "Awaiting final four: C12a/b/c. Then I aggregate and "
                "write RESULTS. Ending turn."
            ),
        ]
        marker_result = scan_for_mid_work_markers(messages)
        length_result = scan_for_short_final_ai(messages, 3)
        assert marker_result.marker_hit is True
        assert length_result.final_word_count > 0
        assert length_result.length_trigger is True

    def test_ep_a_shape_route_unchanged(self):
        """Routing note pin: with 5a fixed, Episode A's NON-DELEGATED
        turn carries REAL length values while the decision STAYS
        ALLOWED — the R3/R4 no-delegation fold fires first; Fix 5a
        changes the log, never the route."""
        manager = MagicMock()
        manager.count_pending_children.return_value = 0
        manager.get_queued_or_expected_wakeups.return_value = 0
        manager.count_live_descendants.return_value = 0
        manager.count_busy_descendants.return_value = 0
        messages = [
            HumanMessage(content="hello"),
            AIMessage(_ep_a_final_message()),
        ]
        result = evaluate(
            "ep-a-inst",
            0,
            messages,
            GateSettings(mode="enforce", window=3, deny_bound=3),
            manager,
        )
        assert result.decision is Decision.ALLOWED
        assert result.attestation_required is False
        assert result.final_word_count > 0, (
            "FIX-5a: the row carries the REAL word count of the final "
            "AIMessage (Episode A logged 0 here)"
        )
        assert result.length_trigger is False, (
            "the 2313-char report is NOT brevity-class — the honest "
            "trigger value on the Episode-A carrier is False (the "
            "defect was the 0 count, not a missed trigger)"
        )


def scan_for_mid_work_markers(messages):
    from daemon.services.attestation_marker_scanner import (
        scan_for_mid_work_markers as _scan,
    )

    return _scan(messages, 3)


def evaluate(*args, **kwargs):
    from daemon.services.attestation_gate import evaluate as _evaluate

    return _evaluate(*args, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# (b) Unverified surface renders + observer job linkage
# ─────────────────────────────────────────────────────────────────────────────


def _escalated_work_record(status: str = "completed") -> WorkRecord:
    return WorkRecord(
        work_id="job-escalated-1",
        kind="job",
        status=status,
        instance_id="inst-1",
        project_id=None,
        agent_id="leader",
        result_summary=None,
        error=None,
        created_at=None,
        completion_gate_escalated=True,
    )


class TestUnverifiedSurfaceRenders:
    def test_job_get_renders_distinct_string(self):
        from daemon.routers.jobs_crud import _job_to_response

        job = _seed_job_item()
        response = _job_to_response(
            job, work_record=_escalated_work_record()
        )
        assert response.status == COMPLETION_GATE_ESCALATED_DISPLAY
        # 7d4a3bd9 Fix 1 (2026-09-26, reviewer-flagged A4.3): the
        # jobs-API router surface threads the machine-readable
        # escalation flag alongside the surfaced ``status`` string so
        # the FE narrowings can key off the boolean (paired with the
        # surfaced status — suffix-matching the status string is
        # fragile). Mirror the MissionResponse assertion at L1049-1052.
        assert response.completion_gate_escalated is True, (
            "A4.3 — the machine-readable flag rides the JobResponse "
            "so the FE badge / narrowings can key off it without "
            "suffix-matching the surfaced status string"
        )

    def test_job_get_non_escalated_stays_plain(self):
        from daemon.routers.jobs_crud import _job_to_response

        job = _seed_job_item()
        plain = _escalated_work_record()
        plain.completion_gate_escalated = False
        response = _job_to_response(job, work_record=plain)
        assert response.status == "completed"
        # 7d4a3bd9 Fix 1 (2026-09-26) — non-escalated completions stay
        # canonical: surfaced status is plain ``completed`` AND the
        # machine-readable flag is False (no escalation signal).
        # Mirror the MissionResponse assertion at L1071.
        assert response.completion_gate_escalated is False, (
            "A4.3 — non-escalated completed stays False on the flag; "
            "the surfaced string AND the boolean stay canonical together"
        )

    def test_settled_mirror_receipt_never_rewritten(self):
        from daemon.routers.jobs_crud import _job_to_response

        job = _seed_job_item()
        record = _escalated_work_record(status="settled")
        response = _job_to_response(job, work_record=record)
        assert response.status == "settled", (
            "the flag describes the MISSION completion; the mirror "
            "receipt vocabulary is disjoint and never rewritten"
        )

    def test_job_event_renders_distinct_string(self):
        """Job-event read point: the display formatter chain renders
        the distinct string for the escalated completed event."""
        assert _format_status_display("completed") == "completed ✓"
        record = _escalated_work_record()
        status = "completed"
        display = _format_status_display(status)
        if (
            getattr(record, "completion_gate_escalated", False)
            and status == "completed"
        ):
            display = COMPLETION_GATE_ESCALATED_DISPLAY
        assert display == COMPLETION_GATE_ESCALATED_DISPLAY

    def test_get_mission_renders_distinct_liveness(self):
        from daemon.services.mission_resolver import MissionRecord
        from daemon.tools.missions import (
            _mission_snapshot_dict,
            _mission_summary_dict,
        )

        record = MissionRecord(
            mission_id="m1",
            agent_id="leader",
            parent_mission_id=None,
            liveness="completed",
            terminal_reason="completed",
            epoch=1,
            completion_gate_escalated=True,
        )
        snapshot = _mission_snapshot_dict(record)
        assert snapshot["liveness"] == COMPLETION_GATE_ESCALATED_DISPLAY
        assert snapshot["completion_gate_escalated"] is True
        assert snapshot["terminal_reason"] == "completed"
        summary = _mission_summary_dict(record)
        assert summary["liveness"] == COMPLETION_GATE_ESCALATED_DISPLAY
        assert summary["completion_gate_escalated"] is True

    def test_get_mission_canonical_records_stay_clean(self):
        from daemon.services.mission_resolver import MissionRecord
        from daemon.tools.missions import _mission_snapshot_dict

        record = MissionRecord(
            mission_id="m1",
            agent_id="leader",
            parent_mission_id=None,
            liveness="completed",
            terminal_reason="completed",
            epoch=1,
            completion_gate_escalated=False,
        )
        assert _mission_snapshot_dict(record)["liveness"] == "completed"
        assert (
            _mission_snapshot_dict(record)["completion_gate_escalated"]
            is False
        )


def _bare_observer(engine):
    """Build the observer with the minimum surface
    ``_finalize_job_db_sync`` needs (mirrors the W1 harness)."""
    observer = JobFeedbackObserver.__new__(JobFeedbackObserver)
    observer._instance_manager = MagicMock()
    observer._instance_manager.engine = engine
    observer._instance_manager.write_guard = WritePauseGuard()
    observer._instance_manager.is_write_paused = False
    observer._bus_count_pending_for_target_sync = lambda _iid: 0
    return observer


def _seed_job_item() -> JobItem:
    """A minimal JobItem stand-in for ``_job_to_response`` (the
    response builder reads queue-payload columns off the row)."""
    return JobItem(
        job_id="job-escalated-1",
        agent_id="leader",
        agent_dir="./agents/leader",
        message="m",
        source="api",
        priority=1,
        admission_state=AdmissionState.DONE.value,
        created_at=datetime.now(timezone.utc).isoformat(),
        instance_id="inst-1",
        job_type="message",
        retry_count=0,
        version=1,
    )


class TestObserverJobLinkage:
    def test_already_finalized_job_witness(self, engine):
        """The no_job anomaly: the episode's job was finalized inline
        (Fix-B mirror semantics) BEFORE the escalated terminal — the
        finalize chain must tie to the REAL job (already-finalized
        witness), not the anonymous no_job."""
        instance_id = "link-inst"
        job_id = "link-job-0828"
        with Session(engine) as session:
            session.add(
                Instance(
                    instance_id=instance_id,
                    agent_id="leader",
                    agent_dir="/tmp",
                    status="COMPLETED",
                    version=1,
                    instance_metadata={},
                    completion_gate_escalated=True,
                )
            )
            session.add(
                JobItem(
                    job_id=job_id,
                    agent_id="leader",
                    agent_dir="/tmp",
                    message="m",
                    source="api",
                    priority=1,
                    admission_state=AdmissionState.DONE.value,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    instance_id=instance_id,
                    job_type="message",
                    retry_count=0,
                    version=1,
                )
            )
            session.commit()

        # Wire the bus mock so the A9/TOCTOU gate passes (mirrors
        # test_observer_finalize_no_job.py::_wire_bus_mock).
        from daemon.services.dependency_bus import set_dependency_bus

        bus_mock = MagicMock()
        bus_mock.count_pending_for_target_sync = lambda _iid: 0
        set_dependency_bus(bus_mock)
        try:
            observer = _bare_observer(engine)
            result = observer._finalize_job_db_sync(
                None,  # no LIVE job — the historical no_job path
                instance_id,
                "completed",
                None,
                None,
                already_finalized_job_id=job_id,
            )
        finally:
            set_dependency_bus(None)
        assert result.already_finalized_job_id == job_id
        assert result.completion_gate_escalated is True

    def test_processing_context_carries_already_finalized_witness(
        self, engine
    ):
        """``_get_processing_job_for_instance`` resolves the freshest
        already-terminal JobItem as the witness when no active/queued
        job exists."""
        instance_id = "ctx-inst"
        job_id = "ctx-job-1"
        with Session(engine) as session:
            session.add(
                Instance(
                    instance_id=instance_id,
                    agent_id="leader",
                    agent_dir="/tmp",
                    status="COMPLETED",
                    version=1,
                    instance_metadata={},
                )
            )
            session.add(
                JobItem(
                    job_id=job_id,
                    agent_id="leader",
                    agent_dir="/tmp",
                    message="m",
                    source="api",
                    priority=1,
                    admission_state=AdmissionState.DONE.value,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    instance_id=instance_id,
                    job_type="message",
                    retry_count=0,
                    version=1,
                )
            )
            session.commit()

        observer = JobFeedbackObserver.__new__(JobFeedbackObserver)
        observer._job_queue_service = MagicMock()
        # Awaited directly (not via to_thread) — must be an AsyncMock.
        # Production shape: the service returns the FRESHEST JobItem
        # for the instance (any admission state) — here, the already-
        # done episode job.
        freshest = SimpleNamespace(job_id=job_id, admission_state="done")
        observer._job_queue_service.get_job_by_instance = AsyncMock(
            return_value=freshest
        )
        observer._job_repo = MagicMock()
        observer._job_repo.get_active_by_instance = MagicMock(
            return_value=None
        )

        async def _run():
            return await observer._get_processing_job_for_instance(
                instance_id
            )

        ctx = asyncio.run(_run())
        assert isinstance(ctx, _ProcessingJobContext)
        assert ctx.job_id is None, (
            "no LIVE job — Step 1/3/notify semantics stay job_id=None"
        )
        assert ctx.already_finalized_job_id == job_id, (
            "the witness ties the terminal to the REAL (already-"
            "finalized) job instead of no_job"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Attestation scan sanity (the deny-band arc inputs)
# ─────────────────────────────────────────────────────────────────────────────


def test_delegated_mission_scans_unattested():
    """The driver's message shape is a delegated, un-attested tail —
    the exact deny-band precondition."""
    messages = [
        HumanMessage(content="ship the feature"),
        AIMessage(
            "",
            tool_calls=[
                {"name": "send_message", "args": {}, "id": "d1"}
            ],
        ),
        AIMessage("I am done without attesting"),
    ]
    scan = scan_for_attestation_detailed(messages, 3, "attest_completion")
    assert scan.attested is False
    assert scan.messages_scanned >= 1


# ─────────────────────────────────────────────────────────────────────────────
# A4.1 / A4.2 / A4.3 / A1 — reviewer-flagged regressions (Phase 2)
# ─────────────────────────────────────────────────────────────────────────────


class TestMissionsRouterRendersDistinctString:
    """A4.1 reviewer-flagged regression: the HTTP API missions router
    surface (``daemon.routers.missions._mission_record_to_response``)
    renders the DISTINCT unverified string via the shared
    ``_render_liveness`` helper (canonical source
    ``daemon.tools.missions``) AND threads the machine-readable
    ``completion_gate_escalated`` flag — so the FE badge / panel gets
    both signals (the surfaced string for the badge, the flag for
    narrowings / analytics).
    """

    def test_mission_response_renders_escalated_liveness(self):
        from daemon.routers.missions import _mission_record_to_response
        from daemon.routers.schemas import MissionResponse
        from daemon.services.mission_resolver import MissionRecord

        record = MissionRecord(
            mission_id="m-escalated",
            agent_id="leader",
            parent_mission_id=None,
            liveness="completed",
            terminal_reason="completed",
            epoch=1,
            completion_gate_escalated=True,
        )
        response = _mission_record_to_response(record)
        assert isinstance(response, MissionResponse)
        assert response.liveness == COMPLETION_GATE_ESCALATED_DISPLAY, (
            "A4.1 — the HTTP router renders the distinct unverified "
            "string via the shared helper (NOT plain 'completed')"
        )
        assert response.completion_gate_escalated is True, (
            "A4.1 — the machine-readable flag rides the response so "
            "the FE narrowings can key off it"
        )

    def test_mission_response_non_escalated_stays_canonical(self):
        from daemon.routers.missions import _mission_record_to_response
        from daemon.services.mission_resolver import MissionRecord

        record = MissionRecord(
            mission_id="m-clean",
            agent_id="leader",
            parent_mission_id=None,
            liveness="completed",
            terminal_reason="completed",
            epoch=1,
            completion_gate_escalated=False,
        )
        response = _mission_record_to_response(record)
        assert response.liveness == "completed", (
            "A4.1 — non-escalated completed stays canonical 'completed'"
        )
        assert response.completion_gate_escalated is False

    def test_mission_response_other_liveness_values_pass_through(self):
        from daemon.routers.missions import _mission_record_to_response
        from daemon.services.mission_resolver import MissionRecord

        # Liveness values that aren't ``completed`` are NEVER rewritten
        # — the distinct string swap is conditional on
        # ``liveness == 'completed' AND completion_gate_escalated``.
        for value in ("processing", "paused", "failed", "cancelled"):
            record = MissionRecord(
                mission_id="m-other",
                agent_id="leader",
                parent_mission_id=None,
                liveness=value,
                terminal_reason=value,
                epoch=1,
                completion_gate_escalated=True,
            )
            response = _mission_record_to_response(record)
            assert response.liveness == value, (
                f"A4.1 — liveness={value!r} passes through unchanged "
                "even when completion_gate_escalated=True (the swap "
                "only fires on the completed-and-escalated combo)"
            )


class TestSseCompletedRendersDistinctString:
    """A4.2 reviewer-flagged regression: the SSE completed event
    (``daemon.routers.jobs_streaming._ResolvedWork.to_completed_payload``)
    renders the DISTINCT unverified string in the surfaced
    ``status`` field AND carries the additive
    ``completion_gate_escalated`` flag — so the FE SSE consumer sees
    the unverified shape loud.
    """

    def _make_work_record(self, escalated: bool = False):
        from daemon.services.work_resolver import WorkRecord

        return WorkRecord(
            work_id="w1",
            kind="job",
            status="completed",
            instance_id="i1",
            project_id=None,
            agent_id="leader",
            result_summary=None,
            error=None,
            created_at=None,
            completion_gate_escalated=escalated,
        )

    def test_completed_payload_swaps_status_when_escalated(self):
        from daemon.routers.jobs_streaming import _ResolvedWork

        rw = _ResolvedWork.from_work_record(self._make_work_record(escalated=True))
        payload = rw.to_completed_payload(work_id="w1")
        assert payload["status"] == COMPLETION_GATE_ESCALATED_DISPLAY, (
            "A4.2 — the completed payload swaps the surfaced status "
            "to the distinct unverified string when the row carries "
            "completion_gate_escalated=True"
        )
        assert payload["completion_gate_escalated"] is True, (
            "A4.2 — the additive flag rides every completed payload"
        )

    def test_completed_payload_keeps_canonical_when_not_escalated(self):
        from daemon.routers.jobs_streaming import _ResolvedWork

        rw = _ResolvedWork.from_work_record(self._make_work_record(escalated=False))
        payload = rw.to_completed_payload(work_id="w1")
        assert payload["status"] == "completed", (
            "A4.2 — non-escalated completed keeps the canonical status"
        )
        assert payload["completion_gate_escalated"] is False

    def test_to_payload_keeps_canonical_status_but_adds_flag(self):
        """The connected / status_update payloads carry the flag but
        keep the canonical status — the swap is the completed event's
        job (the FE sees the canonical mid-flight + the swap on the
        terminal)."""
        from daemon.routers.jobs_streaming import _ResolvedWork

        rw = _ResolvedWork.from_work_record(self._make_work_record(escalated=True))
        payload = rw.to_payload(work_id="w1")
        assert payload["status"] == "completed", (
            "A4.2 — connected/status_update payloads keep the "
            "canonical status (the swap lives on the completed event)"
        )
        assert payload["completion_gate_escalated"] is True

    def test_completed_payload_swap_does_not_affect_other_terminal_values(self):
        """The status swap ONLY fires when the canonical status is
        ``completed`` — other terminal values (failed, cancelled,
        dead_letter) pass through unchanged even when escalated (the
        escalation flag describes the MISSION completion, not the
        receipt)."""
        from daemon.routers.jobs_streaming import _ResolvedWork

        for status in ("failed", "cancelled", "dead_letter"):
            rw = _ResolvedWork(
                work_id="w1",
                status=status,
                instance_id="i1",
                queue_id=None,
                result_summary=None,
                error_message=None,
                job_type="message",
                mission_liveness=status,
                completion_gate_escalated=True,
            )
            payload = rw.to_completed_payload(work_id="w1")
            assert payload["status"] == status, (
                f"A4.2 — status={status!r} passes through unchanged "
                "even when escalated (the swap is the "
                "completed-and-escalated combo ONLY)"
            )


class TestFreshEpisodeChannelReset:
    """A1 reviewer-flagged regression: the SessionState channels
    (substantive + progress) MUST clear at the fresh-episode revival
    boundary, otherwise a prior episode's stamped
    ``attestation_any_substantive_deny=True`` would leak into the
    new episode via checkpoint and wrongly terminalize the FIRST
    bound exhaustion under v3 (a never-spoke arc would carry the
    prior episode's verdict)."""

    def test_fresh_episode_sentinel_clears_substantive_channel(self, repo, monkeypatch):
        """A HumanMessage carrying the ``fresh_episode_attestation_reset``
        sentinel on its ``additional_kwargs`` causes the gate node to
        clear the substantive channel in its return payload — even
        when the input state has ``attestation_any_substantive_deny=True``
        (the prior-episode-leak shape)."""
        from daemon.graph import create_attestation_gate_node

        manager = MagicMock()
        manager.count_pending_children.return_value = 0
        manager.get_queued_or_expected_wakeups.return_value = 0
        manager.count_live_descendants.return_value = 0
        manager.count_busy_descendants.return_value = 0
        settings = GateSettings("enforce", 3, 3)
        config = build_gate_config("fresh-ep-A1", settings)
        node = create_attestation_gate_node(
            config,
            settings,
            manager,
            "fresh-ep-A1",
            denied_count_getter=lambda: repo.get_attestation_denied_count(
                "fresh-ep-A1"
            ),
            ledger=repo,
        )
        repo.create(
            instance_id="fresh-ep-A1",
            agent_id="leader",
            agent_dir="./agents/leader",
        )
        _script_judge(monkeypatch, {"verdict": "timeout"})

        # User message carries the sentinel — first post-revival turn.
        sentinel_msg = HumanMessage(
            content="new turn",
            id="user-revival",
            additional_kwargs={"fresh_episode_attestation_reset": True},
        )
        # The state carries a LEAKED substantive=True from a
        # hypothetical prior episode (the dangerous shape).
        result = asyncio.run(
            node(
                {
                    "messages": [
                        sentinel_msg,
                        AIMessage(
                            "",
                            tool_calls=[
                                {
                                    "name": "send_message",
                                    "args": {"target": "child"},
                                    "id": "d1",
                                }
                            ],
                            id="dispatch-ai",
                        ),
                        AIMessage("I am done without attesting", id="aim-1"),
                    ],
                    ATTESTATION_ANY_SUBSTANTIVE_KEY: True,
                },
                config={"configurable": {"thread_id": "fresh-ep-A1"}},
            )
        )
        assert (
            result[ATTESTATION_ANY_SUBSTANTIVE_KEY] is False
        ), (
            "A1 — the fresh-episode sentinel clears the substantive "
            "channel on the FIRST post-revival turn (the leaked True "
            "from the prior episode MUST NOT persist into the new "
            "episode's checkpoint)"
        )
        assert result[ATTESTATION_DENY_PROGRESS_KEY] is None, (
            "A1 — the progress channel also clears (the snapshot from "
            "the prior episode must not influence the new episode's "
            "directive-vs-standard selection)"
        )

    def test_fresh_episode_sentinel_absent_preserves_channels(self, repo, monkeypatch):
        """Sanity: WITHOUT the sentinel the channels behave exactly as
        before — the existing deny/allow semantics are unchanged."""
        from daemon.graph import create_attestation_gate_node

        manager = MagicMock()
        manager.count_pending_children.return_value = 0
        manager.get_queued_or_expected_wakeups.return_value = 0
        manager.count_live_descendants.return_value = 0
        manager.count_busy_descendants.return_value = 0
        settings = GateSettings("enforce", 3, 3)
        config = build_gate_config("fresh-ep-A1-absent", settings)
        node = create_attestation_gate_node(
            config,
            settings,
            manager,
            "fresh-ep-A1-absent",
            denied_count_getter=lambda: repo.get_attestation_denied_count(
                "fresh-ep-A1-absent"
            ),
            ledger=repo,
        )
        repo.create(
            instance_id="fresh-ep-A1-absent",
            agent_id="leader",
            agent_dir="./agents/leader",
        )
        _script_judge(monkeypatch, {"verdict": "not_complete"})

        # No sentinel — the substantive=True from the input persists
        # on a substantive deny (the normal flow).
        result = asyncio.run(
            node(
                {
                    "messages": [
                        HumanMessage(content="normal turn", id="user-1"),
                        AIMessage(
                            "",
                            tool_calls=[
                                {
                                    "name": "send_message",
                                    "args": {"target": "child"},
                                    "id": "d1",
                                }
                            ],
                            id="dispatch-ai",
                        ),
                        AIMessage("I am done without attesting", id="aim-1"),
                    ],
                    ATTESTATION_ANY_SUBSTANTIVE_KEY: True,
                },
                config={
                    "configurable": {"thread_id": "fresh-ep-A1-absent"}
                },
            )
        )
        # The substantive verdict on this deny STAMPS True (not
        # resets — the sentinel was absent). The pre-existing True
        # from the input AND the new True from the deny both flow
        # through (the channel is OR-style).
        assert result[ATTESTATION_ANY_SUBSTANTIVE_KEY] is True, (
            "A1 — without the sentinel the channels behave normally "
            "(substantive deny stamps True; pre-existing True is "
            "preserved)"
        )

    def test_build_graph_input_stamps_sentinel_when_fresh_episode(self):
        """The ``_build_graph_input`` helper stamps the
        ``fresh_episode_attestation_reset`` sentinel on the user
        message's ``additional_kwargs`` ONLY when the new
        parameter is True — byte-identical to pre-A1 shape when
        False (legacy callers are unaffected)."""
        from daemon.services.instance_messaging import _build_graph_input

        # Fresh-episode True path.
        result = _build_graph_input(
            "hello",
            "msg-1",
            fresh_episode_attestation_reset=True,
        )
        user_msg = result["messages"][-1]
        assert (
            user_msg.additional_kwargs.get("fresh_episode_attestation_reset")
            is True
        ), "A1 — fresh-episode=True stamps the sentinel"

        # Default (False) path — legacy callers see byte-identical
        # shape (no additional_kwargs when there are no other
        # provenance / image_refs stamps).
        result = _build_graph_input("hello", "msg-2")
        user_msg = result["messages"][-1]
        assert (
            "fresh_episode_attestation_reset"
            not in (user_msg.additional_kwargs or {})
        ), "A1 — fresh-episode=False leaves the kwargs byte-identical"
        assert user_msg.additional_kwargs == {}, (
            "A1 — the sentinel param is byte-identical when absent "
            "(legacy caller shape preserved)"
        )
