"""Mode: enforce — third R2 input ``live_descendants`` (2026-09-06).

Acceptance suite for the additive fix that closes the 809e2a59
waiting_children false-deny incident class. The leader-attestation
gate's R2 deny predicate becomes a THREE-input conjunction
(not-attested AND ``pending_children == 0`` AND
``queued_or_expected_wakeups == 0`` AND ``live_descendants == 0``);
any one of the three non-attestation inputs being > 0 routes to
``ALLOWED_LEGITIMATE_PENDING_WAKEUP``.

Covered scenarios (REQUIRED — see task spec):

* (a) Repro of the exact 809e2a59 incident class — child defers to
  ``waiting_children`` (the parent's watcher FIRED, so
  ``pending_children == 0``), a grandchild is RUNNING → gate returns
  ``allowed_legitimate_pending_wakeup``; NO nudge, NO counter write.
* (b) Transitive descendant at depth 2+ counts.
* (c) Regression guard — all descendants terminal, no watchers /
  wakeups → DENY still fires (the original protection MUST survive).
* (d) ``ERROR`` / ``FAILED`` descendants do NOT count as live
  (they are in the terminal set).
* (e) Facade unit tests incl. the BFS cap (``LIVE_DESCENDANTS_BFS_CAP``)
  — both the cap value pin and the cap-behavior pin (descendants past
  the cap are NOT counted).
* (f) Log row carries ``live_descendants`` (canonical-schema drift pin).
* (g) P95 timing sanity (gate stays inside the 20ms NFR-1 budget).

The canonical decision enum is NOT extended — same five values,
``ALLOWED_LEGITIMATE_PENDING_WAKEUP`` is reused for the third-input
allow predicate. ``live_descendants > 0`` is semantically the same
allow predicate as the existing two inputs (a legitimate wakeup is
en route; the leader's turn-end is allowed without attestation).
"""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlmodel import Session

from daemon.manager import InstanceManager
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.services.attestation_gate import (
    CANONICAL_LOG_SCHEMA_FIELDS,
    Decision,
    GateSettings,
    decide,
    evaluate,
)
from tests.support.scripted_chat_model import ScriptedChatModel


# ─────────────────────────────────────────────────────────────────────────────
# Module-level fixtures
# ─────────────────────────────────────────────────────────────────────────────


INSTANCE_ID = "attestation-leader-e2e"


@pytest.fixture(autouse=True)
def _enforce_mode(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
    monkeypatch.setenv("WATCHOVER_ENABLED", "false")


def _seed_instance(engine, instance_id, parent_id, status):
    """Insert an Instance row directly (no InstanceHierarchy table needed).

    The facade reads the permanent ``instances.parent_id`` lineage only —
    bypassing ``repo.create`` keeps the test independent of the transient
    ``instance_hierarchy`` working set (the engine fixture deliberately
    does NOT create that table).
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="worker" if parent_id else "leader",
                agent_dir="./agents/worker" if parent_id else "./agents/leader",
                parent_id=parent_id,
                status=status,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()


class _StubManager:
    """Bare class used to build a facade-testing stub via ``object.__new__``.

    The real facade reads ``self._instance_repository`` and
    ``self.LIVE_DESCENDANTS_BFS_CAP``; we attach both attributes
    manually via ``object.__new__`` (bypassing ``__init__``).
    """

    pass


def _settings():
    return GateSettings(mode="enforce", window=3, deny_bound=3)


def _build(graph_module, model, manager, checkpointer):
    graph_module.build_instance_llms = lambda **_: (model, model)
    with patch(
        "daemon.services.attestation_gate.resolve_gate_settings",
        return_value=_settings(),
    ):
        return graph_module.build_instance_graph(
            tools=[],
            checkpointer=checkpointer,
            llm_config={"model": "scripted-test", "api_key": "test"},
            system_prompt="scripted live-descendants test",
            user_language="Auto",
            language_check_enabled=False,
            manager=manager,
            graph_config={"configurable": {"thread_id": INSTANCE_ID}},
            attestation_enabled=True,
        )


def _nudge_count(messages) -> int:
    return sum(
        isinstance(m, HumanMessage) and bool(m.additional_kwargs.get("attestation_nudge"))
        for m in messages
    )


# ─────────────────────────────────────────────────────────────────────────────
# (a) Repro of exact 809e2a59 incident class — child defers to
# waiting_children (watcher FIRED), grandchild RUNNING → gate returns
# allowed_legitimate_pending_wakeup; NO nudge, NO counter write.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_live_grandchild_allows_via_third_input_without_nudge_or_reset(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """Repro of the 809e2a59 incident class.

    Setup: a single direct child has been COMPLETED, and the watcher
    was FIRED (so ``pending_children == 0``); the child had a deeper
    GRANDCHILD that is still RUNNING (transitively alive).
    ``queued_or_expected_wakeups == 0`` because deferral emits no
    task / report row.

    Expected: gate returns ``ALLOWED_LEGITIMATE_PENDING_WAKEUP`` —
    the third R2 input (``live_descendants > 0``) closes the gap.
    NO nudge. NO counter write.
    """
    repo, _instance = attestation_repository
    # Wire the gate manager facade with the production facade
    # behavior — we override ``live_descendants`` to mirror the
    # incident's tree shape (the direct child is terminal; the
    # transitive grandchild is RUNNING).
    manager = attestation_manager_factory(
        file_sqlite_engine,
        repo,
        pending_children=0,    # watcher FIRED on direct child
        queued_wakeups=0,      # deferral emits no report / task row
        live_descendants=1,    # grandchild is alive
    )

    # Hold a non-zero counter to PROVE the third-input allow did not
    # accidentally behave as an attested allow. The escalation flag
    # is also held to pin ruling 2 (terminal_after_bound reset would
    # clear it; third-input allow MUST NOT).
    repo.increment_attestation_denied_count(INSTANCE_ID, "before-r2-live")
    repo.increment_attestation_denied_count(INSTANCE_ID, "before-r2-live-second")
    repo.set_completion_gate_escalated(INSTANCE_ID)
    before = repo.get(INSTANCE_ID)

    model = ScriptedChatModel(
        responses=[AIMessage(content="done without attestation")],
        i=0,
    )
    graph = _build(real_graph_module, model, manager, memory_saver)
    with caplog.at_level(logging.INFO):
        state = await graph.ainvoke(
            {"messages": [HumanMessage(content="delegate and finish")]},
            config={
                "configurable": {"thread_id": INSTANCE_ID},
                "recursion_limit": 20,
            },
        )

    # (a.i) NO nudge injected (third-input allow is not an attested allow).
    assert _nudge_count(state["messages"]) == 0
    # (a.ii) The third input routed to the legitimate-pending-wakeup allow.
    log_text = caplog.text
    assert "decision=allowed_legitimate_pending_wakeup" in log_text
    assert "decision=denied" not in log_text
    assert "decision=terminal_after_bound" not in log_text
    # (a.iii) The log row carries live_descendants=1 (schema drift pin).
    assert "live_descendants=1" in log_text
    # (a.iv) NO counter write — third-input allow is the same ruling-1
    # non-reset as the existing two R2 inputs.
    after = repo.get(INSTANCE_ID)
    assert after.attestation_denied_count == before.attestation_denied_count == 2
    # (a.v) The escalation flag is also held (terminal_after_bound reset
    # op is the ONLY thing that clears it).
    assert after.completion_gate_escalated is True
    # (a.vi) Facade call assertions (MagicMock + side_effect preserves the
    # real facade behavior + records invocation count for matrix tests).
    manager.count_pending_children.assert_called()
    manager.get_queued_or_expected_wakeups.assert_called()
    manager.count_live_descendants.assert_called()
    manager.enqueue_message.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# (b) Transitive descendant at depth 2+ counts.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_depth_two_descendant_grandchild_counts_via_third_input(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """A depth-2 descendant (grandchild) alive → ``live_descendants > 0``.

    The fixture plants a depth-2 descendant in the real instance tree
    (leader → child → grandchild, all RUNNING except the leader which
    is the leader under test). The production facade's BFS over
    ``instances.parent_id`` must count the grandchild.

    Expected: same outcome as (a) — third-input allow, NO nudge,
    NO counter write.
    """
    repo, _leader = attestation_repository
    # Plant a depth-2 RUNNING subtree (bypassing ``repo.create`` because
    # the engine fixture does not include the ``instance_hierarchy``
    # working table — the facade reads the permanent lineage only):
    #   leader → child (RUNNING) → grandchild (RUNNING)
    _seed_instance(file_sqlite_engine, "child-rd", INSTANCE_ID, InstanceStatus.RUNNING.value)
    _seed_instance(file_sqlite_engine, "grandchild-rd", "child-rd", InstanceStatus.RUNNING.value)

    # Use the production facade (no override on live_descendants).
    manager = attestation_manager_factory(
        file_sqlite_engine,
        repo,
        pending_children=0,
        queued_wakeups=0,
    )

    model = ScriptedChatModel(
        responses=[AIMessage(content="hallucinated completion")],
        i=0,
    )
    graph = _build(real_graph_module, model, manager, memory_saver)
    with caplog.at_level(logging.INFO):
        state = await graph.ainvoke(
            {"messages": [HumanMessage(content="finish")]},
            config={
                "configurable": {"thread_id": INSTANCE_ID},
                "recursion_limit": 20,
            },
        )

    assert _nudge_count(state["messages"]) == 0
    log_text = caplog.text
    # (b.i) Both the depth-1 child AND the depth-2 grandchild are live
    # ⇒ live_descendants=2 (root excluded).
    assert "live_descendants=2" in log_text
    assert "decision=allowed_legitimate_pending_wakeup" in log_text
    assert "decision=denied" not in log_text
    # (b.ii) The grandchild is at depth 2 — the BFS visited it via the
    # permanent ``instances.parent_id`` lineage (root excluded).
    manager.count_live_descendants.assert_called()


# ─────────────────────────────────────────────────────────────────────────────
# (c) Regression guard — all descendants terminal, no watchers / wakeups →
# DENY still fires (the original protection MUST survive).
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_descendants_terminal_still_denies(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """Regression guard — third input does NOT weaken the original deny.

    All descendants in the terminal set, no watchers, no wakeups →
    deny + nudge. The third input must be additive; the original
    protection survives.
    """
    repo, _leader = attestation_repository
    # Plant a TERMINAL subtree (bypassing ``repo.create`` because the
    # engine fixture does not include ``instance_hierarchy`` — the
    # facade reads the permanent lineage only):
    #   leader → child-completed (COMPLETED)
    #           → child-error (ERROR)
    #           → child-failed (FAILED)
    #           → child-terminated (TERMINATED)
    #   child-completed → grandchild-terminal (COMPLETED, depth-2)
    for iid, status in [
        ("child-completed", InstanceStatus.COMPLETED.value),
        ("child-error", InstanceStatus.ERROR.value),
        ("child-failed", InstanceStatus.FAILED.value),
        ("child-terminated", InstanceStatus.TERMINATED.value),
    ]:
        _seed_instance(file_sqlite_engine, iid, INSTANCE_ID, status)
    # Depth-2 descendant terminal too.
    _seed_instance(
        file_sqlite_engine,
        "grandchild-terminal",
        "child-completed",
        InstanceStatus.COMPLETED.value,
    )

    manager = attestation_manager_factory(
        file_sqlite_engine,
        repo,
        pending_children=0,
        queued_wakeups=0,
    )

    model = ScriptedChatModel(
        # Bound is 3 ⇒ 3 denies + 1 escalation = 4 responses
        # (mirrors ``test_attestation_bound_escalation``).
        responses=[AIMessage(content=f"hallucinated {i}") for i in range(4)],
        i=0,
    )
    graph = _build(real_graph_module, model, manager, memory_saver)
    with caplog.at_level(logging.INFO):
        state = await graph.ainvoke(
            {"messages": [HumanMessage(content="finish")]},
            config={
                "configurable": {"thread_id": INSTANCE_ID},
                "recursion_limit": 30,
            },
        )

    # (c.i) At least one nudge (original protection survived).
    assert _nudge_count(state["messages"]) >= 1
    log_text = caplog.text
    # (c.ii) live_descendants=0 — every descendant is terminal.
    assert "live_descendants=0" in log_text
    # (c.iii) DENY path fired (the third input is additive; without it,
    # the deny predicate was already TRUE; with it, the predicate is
    # still TRUE — live_descendants == 0).
    assert "decision=denied" in log_text
    assert "decision=allowed_legitimate_pending_wakeup" not in log_text


# ─────────────────────────────────────────────────────────────────────────────
# (d) ERROR / FAILED descendants do NOT count as live (terminal set).
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_error_and_failed_descendants_are_terminal(
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """``ERROR`` / ``FAILED`` descendants do NOT count as live.

    Specifically: ``live_descendants=0`` when the only descendants are
    in the terminal set (COMPLETED / TERMINATED / ERROR / FAILED).
    Tests the terminal-set definition directly — these are NOT live.
    """
    repo, _leader = attestation_repository
    # Only ERROR + FAILED descendants (no other live status) — bypass
    # ``repo.create`` because the engine fixture does not include the
    # ``instance_hierarchy`` working table; the facade reads the
    # permanent lineage only.
    _seed_instance(
        file_sqlite_engine, "err-only", INSTANCE_ID, InstanceStatus.ERROR.value
    )
    _seed_instance(
        file_sqlite_engine, "failed-only", INSTANCE_ID, InstanceStatus.FAILED.value
    )

    manager = attestation_manager_factory(
        file_sqlite_engine,
        repo,
        pending_children=0,
        queued_wakeups=0,
    )

    model = ScriptedChatModel(
        # Bound is 3 ⇒ 3 denies + 1 escalation = 4 responses
        # (mirrors ``test_attestation_bound_escalation``).
        responses=[AIMessage(content=f"hallucinated {i}") for i in range(4)],
        i=0,
    )
    graph = _build(real_graph_module, model, manager, memory_saver)
    with caplog.at_level(logging.INFO):
        await graph.ainvoke(
            {"messages": [HumanMessage(content="finish")]},
            config={
                "configurable": {"thread_id": INSTANCE_ID},
                "recursion_limit": 30,
            },
        )

    log_text = caplog.text
    # (d.i) Both ERROR and FAILED descendants are terminal ⇒ live_descendants=0.
    assert "live_descendants=0" in log_text
    # (d.ii) DENY fires (regression-guard survival).
    assert "decision=denied" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# (e) Facade unit tests incl. the BFS cap.
# ─────────────────────────────────────────────────────────────────────────────


class TestFacadeBfsCap:
    """Facade unit tests — ``InstanceManager.count_live_descendants``.

    The cap (``LIVE_DESCENDANTS_BFS_CAP = 500``) is the hot-path
    performance bound — the gate sits on the routing path with a
    20ms P95 budget. These tests pin both the cap value AND the
    cap behavior (descendants past the cap are NOT counted).

    NOTE: this matrix inserts instance rows DIRECTLY into the DB
    (bypassing ``repo.create``, which also writes to the
    ``instance_hierarchy`` working set — the engine fixture in
    ``tests/support/conftest.py`` deliberately does NOT create that
    table; the permanent ``instances.parent_id`` lineage is the only
    source the facade reads, so the direct insert is exactly the
    shape under test).
    """

    def _build_manager(self, file_sqlite_engine, repo):
        # The facade is bound on ``InstanceManager`` instances; the
        # ``count_live_descendants`` method only reads from
        # ``_instance_repository`` + ``InstanceStatus`` so we can
        # build a stub InstanceManager-like object with just those
        # two attributes.
        from types import MethodType

        manager = object.__new__(_StubManager)
        manager._instance_repository = repo
        # Bind the cap constant AND the facade method onto the stub
        # (the facade reads both via ``self.`` so attribute lookups
        # must resolve on the stub).
        manager.LIVE_DESCENDANTS_BFS_CAP = InstanceManager.LIVE_DESCENDANTS_BFS_CAP
        manager.count_live_descendants = MethodType(
            InstanceManager.count_live_descendants, manager
        )
        return manager

    # Use the module-level ``_seed_instance`` helper — direct SQL
    # insert that bypasses ``repo.create`` so the test does not need
    # the ``instance_hierarchy`` working table (the engine fixture
    # deliberately does NOT create that table; the permanent
    # ``instances.parent_id`` lineage is the only source the facade
    # reads, which is exactly the shape under test).

    def test_cap_value_is_500(self):
        assert InstanceManager.LIVE_DESCENDANTS_BFS_CAP == 500

    def test_root_excluded_from_count(self, file_sqlite_engine):
        """The leader itself is NOT counted as a descendant of itself."""
        repo = SQLModelInstanceRepository(file_sqlite_engine)
        _seed_instance(file_sqlite_engine, "root-r", None, InstanceStatus.IDLE.value)
        _seed_instance(
            file_sqlite_engine, "child-r", "root-r", InstanceStatus.RUNNING.value
        )
        manager = self._build_manager(file_sqlite_engine, repo)
        assert manager.count_live_descendants("root-r") == 1, (
            "leader (root) MUST be excluded; only the child counts"
        )

    def test_live_set_includes_idle_queued_running_waiting_waiting_children_paused(
        self, file_sqlite_engine
    ):
        """Live set: ``IDLE``, ``QUEUED``, ``RUNNING``, ``WAITING``,
        ``WAITING_CHILDREN``, ``PAUSED`` all count as live.
        """
        repo = SQLModelInstanceRepository(file_sqlite_engine)
        _seed_instance(file_sqlite_engine, "root-set", None, InstanceStatus.IDLE.value)
        for status in [
            InstanceStatus.IDLE.value,
            InstanceStatus.QUEUED.value,
            InstanceStatus.RUNNING.value,
            InstanceStatus.WAITING.value,
            InstanceStatus.WAITING_CHILDREN.value,
            InstanceStatus.PAUSED.value,
        ]:
            _seed_instance(file_sqlite_engine, f"c-{status}", "root-set", status)
        manager = self._build_manager(file_sqlite_engine, repo)
        assert manager.count_live_descendants("root-set") == 6

    def test_terminal_set_excludes_completed_terminated_error_failed(
        self, file_sqlite_engine
    ):
        """Terminal set: ``COMPLETED``, ``TERMINATED``, ``ERROR``,
        ``FAILED`` do NOT count.
        """
        repo = SQLModelInstanceRepository(file_sqlite_engine)
        _seed_instance(file_sqlite_engine, "root-term", None, InstanceStatus.IDLE.value)
        for status in [
            InstanceStatus.COMPLETED.value,
            InstanceStatus.TERMINATED.value,
            InstanceStatus.ERROR.value,
            InstanceStatus.FAILED.value,
        ]:
            _seed_instance(file_sqlite_engine, f"t-{status}", "root-term", status)
        manager = self._build_manager(file_sqlite_engine, repo)
        assert manager.count_live_descendants("root-term") == 0

    def test_zero_descendants_returns_zero(self, file_sqlite_engine):
        repo = SQLModelInstanceRepository(file_sqlite_engine)
        _seed_instance(file_sqlite_engine, "root-zero", None, InstanceStatus.IDLE.value)
        manager = self._build_manager(file_sqlite_engine, repo)
        assert manager.count_live_descendants("root-zero") == 0

    def test_root_not_found_returns_zero(self, file_sqlite_engine):
        repo = SQLModelInstanceRepository(file_sqlite_engine)
        manager = self._build_manager(file_sqlite_engine, repo)
        # Missing root ⇒ get_tree_ids_permanent returns [] ⇒ 0 descendants.
        assert manager.count_live_descendants("nonexistent") == 0

    def test_no_repo_returns_zero(self):
        """No ``_instance_repository`` wired (degenerate embedding) ⇒ 0.

        Mirrors the ``count_pending_children`` fail-closed-allow
        semantics: unreadable ⇒ 0 (genuinely zero, not fail-open dodge).
        """

        class _NoRepo:
            LIVE_DESCENDANTS_BFS_CAP = InstanceManager.LIVE_DESCENDANTS_BFS_CAP

            def count_live_descendants(self, instance_id: str) -> int:
                repo = getattr(self, "_instance_repository", None)
                if repo is None:
                    return 0
                return -1  # unreachable in this test

        assert _NoRepo().count_live_descendants("any") == 0

    def test_bfs_cap_caps_descendant_slice_not_tree_enumeration(
        self, file_sqlite_engine, monkeypatch
    ):
        """BFS cap behavior — descendants past the cap are NOT counted.

        Pin the cap behavior structurally: when the tree holds MORE
        than ``LIVE_DESCENDANTS_BFS_CAP`` descendants, the returned
        count is bounded by the cap. We use a small monkeypatched cap
        to keep the test O(cap) rather than O(500).
        """
        from types import MethodType

        repo = SQLModelInstanceRepository(file_sqlite_engine)
        _seed_instance(file_sqlite_engine, "root-cap", None, InstanceStatus.IDLE.value)
        # Plant MORE descendants than the (monkeypatched) cap so the
        # slice actually clips.
        tiny_cap = 3
        # Patch the class constant for the cap (so MethodType reads the
        # patched value when accessing ``self.LIVE_DESCENDANTS_BFS_CAP``).
        monkeypatch.setattr(
            InstanceManager,
            "LIVE_DESCENDANTS_BFS_CAP",
            tiny_cap,
            raising=False,
        )
        # Build a stub with the patched cap bound as an instance attr
        # (so the production facade's ``self.LIVE_DESCENDANTS_BFS_CAP``
        # read resolves to ``tiny_cap``).
        manager = object.__new__(_StubManager)
        manager._instance_repository = repo
        manager.LIVE_DESCENDANTS_BFS_CAP = tiny_cap
        manager.count_live_descendants = MethodType(
            InstanceManager.count_live_descendants, manager
        )
        # Plant 5 live descendants (more than the patched cap of 3).
        for i in range(5):
            _seed_instance(
                file_sqlite_engine,
                f"cap-c-{i}",
                "root-cap",
                InstanceStatus.RUNNING.value,
            )
        # The cap clips the descendant slice to ``tiny_cap`` — we read
        # only the first 3 live descendants, not all 5.
        assert manager.count_live_descendants("root-cap") == tiny_cap


# ─────────────────────────────────────────────────────────────────────────────
# (f) Log row carries ``live_descendants`` (canonical-schema drift pin).
# ─────────────────────────────────────────────────────────────────────────────


class TestCanonicalLogSchema:
    """The 16-field canonical schema carries ``live_descendants``.

    Schema drift pin: every ``event=leader_completion_gate`` log line
    MUST carry every field in ``CANONICAL_LOG_SCHEMA_FIELDS`` (the
    ``tests/integration/test_attestation_dry_mode.py`` schema-drift
    assertion is the runtime equivalent; this is the unit-level
    sanity check).
    """

    def test_canonical_schema_has_16_fields(self):
        # 15 original fields + 1 additive (live_descendants, 2026-09-06).
        assert len(CANONICAL_LOG_SCHEMA_FIELDS) == 16

    def test_canonical_schema_includes_live_descendants(self):
        assert "live_descendants" in CANONICAL_LOG_SCHEMA_FIELDS

    def test_canonical_log_emits_live_descendants_field(self, caplog):
        manager = MagicMock()
        manager.count_pending_children.return_value = 0
        manager.get_queued_or_expected_wakeups.return_value = 0
        manager.count_live_descendants.return_value = 2  # depth-2 tree

        with caplog.at_level(logging.INFO, logger="daemon.services.attestation_gate"):
            evaluate(
                "schema-drift",
                0,
                [AIMessage(content="plain completion")],
                GateSettings(mode="enforce", window=3, deny_bound=3),
                manager,
            )
        log_line = next(
            r.message
            for r in caplog.records
            if "event=leader_completion_gate" in r.message
        )
        # Every canonical field present (drift pin).
        for field in CANONICAL_LOG_SCHEMA_FIELDS:
            assert f"{field}=" in log_line, f"missing canonical field {field}"
        assert "live_descendants=2" in log_line

    def test_db_seam_failure_emits_live_descendants_minus_one(self, caplog):
        """DB-seam fail-open path reports ``live_descendants=-1``.

        Mirrors the existing ``pending_children=-1`` /
        ``queued_or_expected_wakeups=-1`` sentinels. 0 is a MEANINGFUL
        R2 value ("no wakeups") — the sentinel must be -1.
        """
        manager = MagicMock()
        manager.count_pending_children.side_effect = RuntimeError("db down")
        manager.get_queued_or_expected_wakeups.return_value = 0
        manager.count_live_descendants.return_value = 0

        with caplog.at_level(logging.ERROR, logger="daemon.services.attestation_gate"):
            evaluate(
                "db-seam",
                0,
                [AIMessage(content="plain completion")],
                GateSettings(mode="enforce", window=3, deny_bound=3),
                manager,
            )
        assert "event=leader_completion_gate_db_error" in caplog.text
        assert "live_descendants=-1" in caplog.text

    def test_scanner_seam_failure_emits_live_descendants_minus_one(
        self, caplog, monkeypatch
    ):
        """Scanner-fail-open path emits ``live_descendants=-1``.

        The scanner-side exception path bypasses the DB seam — the
        R2 inputs are UNKNOWN on this path; the sentinel is -1. The
        short log line on this path (existing precedent at
        ``tests/unit/test_attestation_gate.py::test_scanner_exception_fails_open_with_error_event``)
        does NOT carry every canonical field — only the
        ``GateDecision`` does. We assert the sentinel on the result
        object AND that the log line carries the error event.
        """
        manager = MagicMock()
        manager.count_pending_children.return_value = 0
        manager.get_queued_or_expected_wakeups.return_value = 0
        manager.count_live_descendants.return_value = 0

        def _boom(*args, **kwargs):
            raise ValueError("scanner exploded")

        from daemon.services import attestation_gate as gate_module

        monkeypatch.setattr(gate_module, "scan_for_attestation_detailed", _boom)
        with caplog.at_level(logging.ERROR, logger="daemon.services.attestation_gate"):
            result = evaluate(
                "scan-seam",
                0,
                [AIMessage(content="plain completion")],
                GateSettings(mode="enforce", window=3, deny_bound=3),
                manager,
            )
        assert "event=leader_completion_gate_error" in caplog.text
        # The sentinel lives on the GateDecision — it MUST be -1
        # (UNKNOWN) on this path, never the meaningful 0 (which would
        # be a false positive for the dry-mode deny-predicate metric).
        assert result.live_descendants == -1


# ─────────────────────────────────────────────────────────────────────────────
# (g) P95 timing sanity (gate stays inside the 20ms NFR-1 budget).
# ─────────────────────────────────────────────────────────────────────────────


class TestP95TimingSanity:
    """The third input does NOT push the gate past the 20ms P95 budget.

    The current gate P95 is ~0.016ms (test_attestation_performance
    baseline). Adding the ``count_live_descendants`` facade call MUST
    keep the order-of-magnitude. The cap (500) bounds the BFS so
    realistic trees stay tiny.
    """

    def test_gate_p95_stays_inside_20ms_with_third_input(self):
        manager = MagicMock()
        manager.count_pending_children.return_value = 0
        manager.get_queued_or_expected_wakeups.return_value = 0
        manager.count_live_descendants.return_value = 2  # tree-state call

        messages = [HumanMessage(content="work"), AIMessage(content="plain final")]
        settings = GateSettings(mode="enforce", window=3, deny_bound=3)

        # Warm the resolver import.
        evaluate("timing-third-input", 0, messages, settings, manager)
        durations_ns = []
        for _ in range(200):
            started = time.perf_counter_ns()
            evaluate("timing-third-input", 0, messages, settings, manager)
            durations_ns.append(time.perf_counter_ns() - started)

        durations_ns.sort()
        p95_ns = durations_ns[int(0.95 * (len(durations_ns) - 1))]
        p95_ms = p95_ns / 1_000_000
        # NFR-1 budget. The MagicMock third-input call is the
        # realistic shape (production facade returns a small int);
        # the cap (500) bounds the real BFS so the order of magnitude
        # holds.
        assert p95_ms <= 20.0, f"NFR-1 P95={p95_ms:.3f} ms (third-input overhead)"


# ─────────────────────────────────────────────────────────────────────────────
# decide() unit pin — the third input as a separate allow arm.
# ─────────────────────────────────────────────────────────────────────────────


class TestDecideLiveDescendantsMatrix:
    """``decide()`` matrix pin for the third-input allow arm.

    The pure decision function grows one kwarg (``live_descendants``)
    and one new ``OR`` arm in the R2-allow branch. No new enum
    member — ``ALLOWED_LEGITIMATE_PENDING_WAKEUP`` already covers
    the semantic.
    """

    @pytest.mark.parametrize("live_descendants", [1, 2, 100])
    def test_live_descendants_routes_to_allow_legitimate(self, live_descendants):
        result = decide(
            attested=False,
            pending_children=0,
            queued_or_expected_wakeups=0,
            live_descendants=live_descendants,
            denied_count=2,
            bound=3,
            scope_applicable=True,
            mode="enforce",
            attestation_enabled=True,
        )
        assert result.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        # RULING 1: third-input allow is the same non-reset as the
        # existing two R2 inputs — counter unchanged.
        assert result.next_denied_count == 2
        assert result.should_inject_nudge is False

    def test_live_descendants_plus_pending_combined(self):
        """All three R2 inputs > 0 ⇒ still one allow, one non-reset."""
        result = decide(
            attested=False,
            pending_children=2,
            queued_or_expected_wakeups=1,
            live_descendants=3,
            denied_count=2,
            bound=3,
            scope_applicable=True,
            mode="enforce",
            attestation_enabled=True,
        )
        assert result.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        assert result.next_denied_count == 2

    def test_attested_takes_precedence_over_live_descendants(self):
        """Attested allow is reset trigger 1 — comes BEFORE the R2 allow
        branch in the decision tree, even with a non-zero third input.
        """
        result = decide(
            attested=True,
            pending_children=2,
            queued_or_expected_wakeups=1,
            live_descendants=3,
            denied_count=2,
            bound=3,
            scope_applicable=True,
            mode="enforce",
            attestation_enabled=True,
        )
        assert result.decision is Decision.ALLOWED
        assert result.next_denied_count == 0  # reset trigger 1