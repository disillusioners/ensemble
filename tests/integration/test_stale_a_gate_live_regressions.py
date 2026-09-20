"""LCA stale-A fix (B1) — INDEPENDENT live regressions at the integration seam.

Real-gate-node integration tests driving ``create_attestation_gate_node``
directly with a pre-built ``state["messages"]`` containing the
scenario's child-report HumanMessages stamped with the exact
``internal_report:<child_iid>:<completed_message_id>`` shape the
resolver scans. The fix is verified END-TO-END: messages → gate's
``evaluate()`` → ``evaluate_resolver_activation`` → fused bundle
assembly → judge stub (recording the real bundle text).

Construction rule: scenarios here are INDEPENDENTLY built from the
incident specs (acbf5627 / child-lie counterweight / newest-only
clean-newest). The harness mechanics (gate-node factory,
manager-stub shape with the four R2 facades, judge-stub recording
pattern) follow the existing attestation graph fixture
infrastructure — the support fixtures
(``real_graph_module``, ``file_sqlite_engine``, ``attestation_repository``,
``attestation_manager_factory``) wire the production manager facade
methods and a real SQLite instance repository. The dev's unit tests
at ``tests/unit/test_attestation_resolver_activation.py`` are NOT
referenced (they pin the production code; this file pins the
MERGE-GATE behavior at the gate-node seam).

Production code is FROZEN — this file is test-only, new file, does
NOT modify any existing test.
"""
from __future__ import annotations

import logging
import re
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.services.attestation_report_judge import FusedJudgeResult


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures: per-scenario instance ids + child ids
# ─────────────────────────────────────────────────────────────────────────────


# Distinct instance ids per scenario so the ledger (per-instance
# counter) stays isolated across the three integration scenarios.
INSTANCE_ID_ACBF = "stale-a-acbf5627-e2e"
INSTANCE_ID_CHILD_LIE = "stale-a-child-lie-e2e"
INSTANCE_ID_CLEAN_NEWEST = "stale-a-clean-newest-e2e"

# Distinct child ids — the resolver's cross-resolution consumes
# instance_id from the tree rows.
GITER_CHILD_ID = "aaaa1111-bbbb-2222-cccc-333344445555"
WORKER_CHILD_ID = "eeee4444-ffff-5555-aaaa-666677778888"

# Distinctive sentinel fragments used to assert bundle presence
# (id-redaction makes the child uuids vanish in the bundle; we
# therefore tag content with sentinel strings to prove the bundle
# includes / excludes the latest report).
PHASE_STOP_SENTINEL = "GITER_PHASE_STOP_SENTINEL"
LIE_SENTINEL = "WORKER_LIE_SENTINEL"
CLEAN_SENTINEL = "GITER_CLEAN_DELIVERY_SENTINEL"
PENDING_SENTINEL = "WORKER_PENDING_SENTINEL"


# ─────────────────────────────────────────────────────────────────────────────
# Test-local fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _enforce_mode(monkeypatch):
    """Pin mode=enforce + fused LLM judge kill-switch ON; clear the
    resolver cache so this module always re-resolves to default-ON."""
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
    monkeypatch.setenv("WATCHOVER_ENABLED", "false")
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", raising=False
    )
    from daemon.services.attestation_judge_resolver import (
        reset_llm_judge_resolver_for_tests,
    )

    reset_llm_judge_resolver_for_tests()


def _settings():
    from daemon.services.attestation_gate import GateSettings

    return GateSettings(mode="enforce", window=3, deny_bound=3)


def _judge_recording_stub(verdict: str, captured: list[str]):
    """Build a graph-seam judge stub that RECORDS the REAL bundle text.

    Mirrors :func:`judge_fused_bundle_async`'s contract (positional
    bundle_text, keyword config/timeout_s; never raises) and returns a
    fully-populated :class:`FusedJudgeResult` so the graph's derived
    ``judge_invoked`` flag is exercised for real.
    """
    is_complete = verdict == "complete"

    async def _stub(bundle_text: str, *, config, timeout_s=None) -> FusedJudgeResult:
        captured.append(bundle_text)
        return FusedJudgeResult(
            invoked=True,
            is_complete=is_complete,
            verdict=verdict,
            rationale=(
                "stub: cleared-advisory bundle looks complete"
                if is_complete
                else "stub: kept-advisory bundle flagged"
            ),
            model="stub-stale-a-judge",
            latency_ms=1,
            attempt=1,
        )

    return _stub


def _arm_log_capture(caplog) -> None:
    """INFO capture on the ROOT logger (graph + service loggers)."""
    caplog.set_level(logging.INFO)


def _resolver_eval_row_text(caplog) -> str:
    """Return the ONE FIRED leader_completion_resolver_eval row text."""
    rows = [
        record.getMessage()
        for record in caplog.records
        if "event=leader_completion_resolver_eval " in record.getMessage()
        and " fired=True " in record.getMessage()
    ]
    assert rows, "expected the fired resolver eval row; none was emitted"
    assert len(rows) == 1, f"expected ONE fired resolver eval row, got {len(rows)}"
    return rows[0]


# ─────────────────────────────────────────────────────────────────────────────
# Builders
# ─────────────────────────────────────────────────────────────────────────────


def _internal_report(content: str, child_id: str) -> HumanMessage:
    """Build a live child completion-report HumanMessage with the
    exact stamp shape the resolver scans."""
    source = f"internal_report:{child_id}:{uuid.uuid4()}"
    return HumanMessage(
        content=content,
        id=str(uuid.uuid4()),
        additional_kwargs={
            "injected_message": True,
            "source": source,
        },
    )


def _delegated_state(
    final_text: str, *reports: HumanMessage
) -> list:
    """User question → delegation AIMessage (send_message tool_call) →
    final AIMessage + N stamped child-report HumanMessages."""
    return [
        HumanMessage(content="Ship the LCA stale-A fix."),
        AIMessage(
            content="Delegating.",
            tool_calls=[
                {"name": "send_message", "args": {"target": "giter"}, "id": "d-1"}
            ],
        ),
        AIMessage(content=final_text),
        *reports,
    ]


def _seed_completed_child(repo, child_id: str, *, agent_id: str = "giter") -> None:
    """Seed a completed child row in the instance repository so the
    gate's tree-rows provider (built via ``make_tree_rows_provider``)
    surfaces the child with status ``completed`` at evaluation time."""
    try:
        repo.create(
            instance_id=child_id,
            agent_id=agent_id,
            agent_dir=f"./agents/{agent_id}",
        )
    except Exception:
        # Idempotent: if a row exists from a previous test, ignore.
        pass

    from sqlmodel import Session, select
    from daemon.repositories.instance.models import Instance

    engine = repo.engine
    with Session(engine) as session:
        row = session.scalar(
            select(Instance).where(Instance.instance_id == child_id)
        )
        if row is not None:
            row.status = "completed"
            session.add(row)
            session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Gate-node driver — direct invocation, no full graph build
# ─────────────────────────────────────────────────────────────────────────────


async def _run_gate_node(
    graph_module,
    manager,
    instance_id: str,
    messages: list,
    verdict: str,
    captured_bundles: list[str],
):
    """Drive ``create_attestation_gate_node`` directly with a pre-built
    state. Returns the gate node's return dict (the per-band outcome
    + the log rows already on the caplog if attached).

    The judge stub is patched at the graph-seam module attribute —
    the ONE judge call site imports its callable at call time, so
    patching the module attribute intercepts the real invocation with
    the REAL bundle text in hand.
    """
    from daemon.services.attestation_gate import build_gate_config

    settings = _settings()
    gate_config = build_gate_config(
        instance_id=instance_id,
        settings=settings,
        tool_name="attest_completion",
        leader_prompt_version="v1",
        llm_judge_enabled=True,
    )
    gate_node = graph_module.create_attestation_gate_node(
        gate_config=gate_config,
        settings=settings,
        manager=manager,
        instance_id=instance_id,
        denied_count_getter=lambda: 0,
        ledger=None,
    )

    state = {"messages": messages}
    config = {"configurable": {"thread_id": instance_id}}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "daemon.services.attestation_report_judge.judge_fused_bundle_async",
            _judge_recording_stub(verdict, captured_bundles),
        )
        return await gate_node(state, config)


# ─────────────────────────────────────────────────────────────────────────────
# Scenario A — acbf5627 shape (real gate node, quiet tree)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scenario_a_acbf5627_quiet_tree_passes_without_stale_a_hold(
    real_graph_module,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """SCENARIO A — acbf5627 shape at the real gate node.

    Quiet tree + child whose FIRST report was a phase-stop promise +
    LATER report was a clean delivery + tree row marks the child
    completed. The fix MUST clear the stale advisory so the final
    quiet-tree eval passes WITHOUT a deny or a nudge — the leader's
    clean delivery was real, the stale "will report back" was
    superseded.
    """
    repo, _instance = attestation_repository
    manager = attestation_manager_factory(
        file_sqlite_engine, repo, pending_children=0, queued_wakeups=0
    )

    # Seed the tree row: giter is completed. The resolver's
    # ``make_tree_rows_provider`` enumerates the leader's subtree via
    # ``manager.get_tree_ids_permanent`` and reads each row's status.
    _seed_completed_child(repo, GITER_CHILD_ID, agent_id="giter")

    phase_stop = _internal_report(
        f"{PHASE_STOP_SENTINEL} Phase 1 merged. Will report back after phase 2.",
        child_id=GITER_CHILD_ID,
    )
    clean_delivery = _internal_report(
        f"All shipped, no follow-ups. {CLEAN_SENTINEL}",
        child_id=GITER_CHILD_ID,
    )

    final_text = "Done. The giter subtree is closed out; the merge is shipped."
    messages = _delegated_state(final_text, phase_stop, clean_delivery)

    captured: list[str] = []
    _arm_log_capture(caplog)
    result = await _run_gate_node(
        real_graph_module,
        manager,
        INSTANCE_ID_ACBF,
        messages,
        verdict="complete",
        captured_bundles=captured,
    )

    # The judge was invoked EXACTLY once over the REAL assembled bundle.
    assert len(captured) == 1, (
        f"acbf5627: judge must be invoked exactly once on the deny band; "
        f"got {len(captured)} (missing = predicate didn't fire)"
    )

    eval_row_text = _resolver_eval_row_text(caplog)
    assert "a_advisory_present=False" in eval_row_text, (
        f"acbf5627: resolver eval row must show a_advisory_present=False "
        f"(stale advisory cleared); got: {eval_row_text}"
    )
    assert "a_notes=0" in eval_row_text, (
        f"acbf5627: no evidence rows must survive; got: {eval_row_text}"
    )
    # Quiet-tree deny band: only c_quiet term.
    assert "terms_fired=c_quiet" in eval_row_text, (
        f"quiet-tree deny band: terms_fired must be c_quiet alone; "
        f"got: {eval_row_text}"
    )

    bundle_text = captured[0]
    assert "SOURCE A: child-terminal contradiction evidence" in bundle_text
    assert "(no Child Report Check notes delivered)" in bundle_text, (
        "bundle A section must reflect the cleared advisory "
        "(empty-state line, not an evidence row)"
    )
    # The cleared child uuid MUST NOT leak into the bundle.
    assert GITER_CHILD_ID not in bundle_text

    # The judge ran with verdict=complete on the cleared deny band →
    # the real gate node returns the rescue path (allow END, no
    # attestation nudge, no counter movement).
    assert "fused-judge rescue" in caplog.text
    assert result.get("attestation_route") is None


# ─────────────────────────────────────────────────────────────────────────────
# Scenario B — child-lie counterweight (real gate node, busy tree)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scenario_b_child_lie_completed_without_delivery_a_band_fires(
    real_graph_module,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """SCENARIO B — child-lie counterweight.

    Completed child whose NEWEST report genuinely promises undelivered
    work ("will report back after the follow-up merge"). The fix must
    NOT over-suppress: the later-contradiction exception engages and
    the A-band holds the leader on a busy tree.

    The judge stub returns NOT complete so the resolver doesn't rescue
    into ALLOW. We assert on the real gate output: the A-band engaged
    on the bundle A section + the resolver eval row reflects the keep.
    """
    repo, _instance = attestation_repository
    # Busy tree: live_descendants=1 keeps the predicate from going
    # quiet (so the A-band wins over the deny band, exercising the
    # Δ2 row — Source A is NOT busy-suppressed).
    manager = attestation_manager_factory(
        file_sqlite_engine,
        repo,
        pending_children=0,
        queued_wakeups=0,
        live_descendants=1,
    )

    # Completed child whose newest report is a genuine (non-operator-
    # scoped) lie — "will report back after the follow-up merge".
    _seed_completed_child(repo, WORKER_CHILD_ID, agent_id="worker")

    child_lie = _internal_report(
        f"Merged the branch. {LIE_SENTINEL} Will report back after the follow-up merge.",
        child_id=WORKER_CHILD_ID,
    )

    final_text = "Reviewer is done; nothing else pending on my side."
    messages = _delegated_state(final_text, child_lie)

    captured: list[str] = []
    _arm_log_capture(caplog)
    await _run_gate_node(
        real_graph_module,
        manager,
        INSTANCE_ID_CHILD_LIE,
        messages,
        verdict="not_complete",
        captured_bundles=captured,
    )

    # The judge was invoked EXACTLY once — on the A-band plan.
    assert len(captured) == 1, (
        f"child-lie: judge must be invoked exactly once on the A-band; "
        f"got {len(captured)}"
    )

    # The bundle's A section MUST carry the kept evidence row — proving
    # the later-contradiction exception engaged for the completed child.
    bundle_text = captured[0]
    assert "SOURCE A: child-terminal contradiction evidence" in bundle_text
    assert LIE_SENTINEL in bundle_text, (
        "child-lie: the newest report content MUST reach the bundle "
        "(later-contradiction exception surface)"
    )

    # The resolver eval row reflects the keep.
    eval_row_text = _resolver_eval_row_text(caplog)
    assert "a_advisory_present=True" in eval_row_text, (
        f"child-lie: resolver eval row must show a_advisory_present=True; "
        f"got: {eval_row_text}"
    )
    assert "a_suspicion" in eval_row_text, (
        f"child-lie: a_suspicion term must appear in terms_fired; "
        f"got: {eval_row_text}"
    )
    assert "judge_verdict=not_complete" in eval_row_text
    # The kept evidence row carries the (redacted) child id + the
    # matched terms; the genuine "will report back" appears verbatim
    # in the bundle.
    assert "will report back" in bundle_text


# ─────────────────────────────────────────────────────────────────────────────
# Scenario C — newest-only clean-newest (real gate node)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scenario_c_newest_clean_with_early_promise_no_hold(
    real_graph_module,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """SCENARIO C — newest-only semantics.

    Completed child whose EARLIER report promised follow-up work and
    whose NEWEST (transcript order) report is clean. The earlier
    advisory MUST NOT resurface — the fix's newest-only rule clears
    it. Quiet tree → gate passes without stale-A hold."""
    repo, _instance = attestation_repository
    manager = attestation_manager_factory(
        file_sqlite_engine, repo, pending_children=0, queued_wakeups=0
    )

    _seed_completed_child(repo, GITER_CHILD_ID, agent_id="giter")

    early_promise = _internal_report(
        f"{PENDING_SENTINEL} Still pending integration, ending turn.",
        child_id=GITER_CHILD_ID,
    )
    clean_newest = _internal_report(
        f"Integration complete. All checks green. {CLEAN_SENTINEL}",
        child_id=GITER_CHILD_ID,
    )

    final_text = "Wrap-up complete. All shipped."
    messages = _delegated_state(final_text, early_promise, clean_newest)

    captured: list[str] = []
    _arm_log_capture(caplog)
    result = await _run_gate_node(
        real_graph_module,
        manager,
        INSTANCE_ID_CLEAN_NEWEST,
        messages,
        verdict="complete",
        captured_bundles=captured,
    )

    # The judge was invoked EXACTLY once.
    assert len(captured) == 1, (
        f"newest-only: judge must be invoked exactly once; got {len(captured)}"
    )

    eval_row_text = _resolver_eval_row_text(caplog)
    assert "a_advisory_present=False" in eval_row_text, (
        f"newest-only: the earlier report's advisory must NOT resurface "
        f"when the newest report is clean; got: {eval_row_text}"
    )
    assert "a_notes=0" in eval_row_text

    bundle_text = captured[0]
    assert "SOURCE A: child-terminal contradiction evidence" in bundle_text
    assert "(no Child Report Check notes delivered)" in bundle_text, (
        "bundle A section must reflect the cleared advisory "
        "(newest-only semantics: clean newest overrides earlier hit)"
    )
    # The early report's distinctive sentinel must NOT reach the bundle
    # (only the newest report is scanned, and the newest is clean).
    assert PENDING_SENTINEL not in bundle_text, (
        "the EARLIER report's content must NOT reach the bundle — "
        "newest-only semantics dropped it wholesale"
    )

    # Real gate outcome: judge-complete rescue → ALLOW, no nudge.
    assert "fused-judge rescue" in caplog.text
    assert result.get("attestation_route") is None
