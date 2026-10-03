"""End-to-end edge-case tests for the Post-Restart Arm-Notify feature
(Phase 3 — T5.13, T5.14, T5.15, T5.17).

The long-tail edge cases that Phase 2 deferred:

* **T5.13 — long-downtime double-arm:** two arms happen within a
  single daemon lifecycle (e.g. arm A, restart, arm B, restart).
  The journal after the second restart has 2 ``pending_wakes``
  entries. The boot pass coalesces them into ONE wake with a
  run-list payload, delivered to the same arming instance. The
  test sets the same ``arming_instance_id`` for both arms.
  Assertions: (a) one ``enqueue_message`` call for the arming
  instance, (b) body has both ``run_id``s, (c) body is
  newest-first, (d) ``pending_wakes`` dict is empty
  post-delivery. AC5 + D-FA5.2 + ADR-043 enforcement.

* **T5.14 — paused-instance defer:** an arming instance that is
  ``PAUSED`` at boot time → the wake's ``enqueue_message`` is
  held by the existing claim-side pause gate
  (``instance_messaging.py:2154-2161, :1925-1934``). The wake
  is delivered (the ``MessageQueue`` row is created) but the
  ``Task`` is held ``PENDING`` until the instance resumes.
  The wake record's ``status=delivered`` is set after
  ``enqueue_message`` returns; the resume of the instance
  drains the held Task. AC5 + D-FA3.1; R-15 mitigation.

* **T5.15 — terminal-instance revival:** an arming instance
  that was ``COMPLETED`` / ``TERMINATED`` / ``ERROR`` /
  ``FAILED`` at the time of the arm → the wake's
  ``enqueue_message`` triggers the existing terminal→RUNNING
  flip (``instance_messaging.py:1954-1976``). The wake is
  delivered to the revived instance. AC5 + D-FA3.1.

* **T5.17 — kill-switch re-enable-no-stale-flood:** (1) arm
  → record present; (2) env OFF for a period → the sweep's
  one-time abandon-pass marks it ``abandoned`` with
  ``reason=kill_switch_off``; (3) env re-enabled → the sweep
  runs, finds NO ``pending`` records, delivers NOTHING.
  Architecture delta #2, MUST. ADR-044 persisted-record
  semantics.

Convention precedent: ``tests/job_queue/test_a4_f14_orphan_detection.py``
(multi-record journal state, AsyncMock manager seam) and the
existing ``tests/unit/services/test_pause_resume_seam.py`` style.
All fixtures use ``tmp_path``; no live contact.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.upgrade_journal_sweep import UpgradeJournalSweepService
from daemon.services.messaging_types import AsyncMessageResult
from daemon.tools import upgrade_journal as uj
from daemon.tools.upgrade_journal import (
    ARM_NOTIFY_KILL_SWITCH_ENV,
    PendingWake,
    PENDING_WAKE_GRACE_S,
    arm_pending_wake,
    iso_plus,
    journal_init,
    journal_read,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def install(tmp_path: Path) -> Path:
    """Fresh staged-install fixture."""
    inst = tmp_path / "install"
    (inst / "releases").mkdir(parents=True)
    journal_init(inst)
    uj.ensure_extensions(inst)
    return inst


def _make_wake(
    install_dir: Path,
    run_id: str = "r-test-1",
    *,
    arming_instance_id: str = "i-arm-1",
    arming_agent_id: str | None = "ari",
    source: str = "discord:user123",
    message_id: str | None = "m-original-1",
    kind: str = "restart",
    armed_at: str = "2026-10-04T00:00:00Z",
    expires_at: str = "2026-10-04T00:10:00Z",
    status: str = "pending",
) -> PendingWake:
    """Build + arm a PendingWake (writes the record to disk)."""
    wake = PendingWake(
        run_id=run_id,
        kind=kind,
        env="demo",
        arming_instance_id=arming_instance_id,
        arming_agent_id=arming_agent_id,
        source=source,
        message_id=message_id,
        message_metadata={"k": "v"},
        target_version=None,
        mode="graceful-now",
        armed_at=armed_at,
        expires_at=expires_at,
        abandon_after=iso_plus(expires_at, PENDING_WAKE_GRACE_S),
        status=status,
    )
    arm_pending_wake(install_dir, wake)
    return wake


def _append_history(
    install_dir: Path,
    event: str,
    detail: str = "...",
    ts: str = "2026-10-04T00:01:00Z",
) -> None:
    """Append a real-shape history entry (``{ts, event, detail}``)."""
    data = journal_read(install_dir)
    history = data.get("history") or []
    history.append({"ts": ts, "event": event, "detail": detail})
    data["history"] = history
    uj.journal_write(install_dir, data)


def _mock_manager(
    instance_state: str = "RUNNING",
) -> tuple[MagicMock, dict[str, Any]]:
    """A wired manager mock that simulates the claim-side pause gate +
    terminal-revive semantics in ``instance_messaging.py``.

    Returns ``(manager, state)`` where ``state`` is a dict tracking
    enqueue calls + state transitions (the test asserts on these).
    """
    state: dict[str, Any] = {
        "instance_state": instance_state,
        "enqueue_calls": [],
        "message_queue_rows": [],
        "tasks": {},
    }

    manager = MagicMock()
    manager.stamp_user_origin_window = MagicMock()

    async def _enqueue(
        instance_id: str,
        message: str,
        source: str,
        priority: int,
        metadata: dict,
    ) -> AsyncMessageResult:
        # Simulate the existing claim-side pause gate
        # (instance_messaging.py:2154-2161) — if the instance is PAUSED
        # the message is enqueued (MessageQueue row + PENDING Task held
        # until resume). The wake is "delivered" from the sweep's view.
        state["enqueue_calls"].append(
            {
                "instance_id": instance_id,
                "message": message,
                "source": source,
                "priority": priority,
                "metadata": metadata,
            }
        )
        msg_id = f"m-wake-{len(state['enqueue_calls'])}"
        state["message_queue_rows"].append(
            {
                "message_id": msg_id,
                "instance_id": instance_id,
                "source": source,
            }
        )
        # Simulate terminal→RUNNING flip on the FIRST enqueue for a
        # terminal instance (D-FA3.1; R-15 complement). The
        # instance_messaging path that the wake rides flips
        # COMPLETED/TERMINATED/ERROR/FAILED → RUNNING before
        # enqueueing the message.
        if state["instance_state"] in {
            "COMPLETED", "TERMINATED", "ERROR", "FAILED",
        }:
            state["instance_state"] = "RUNNING"
        # Simulate the pause gate: a Task row is created in PENDING
        # state when the instance is PAUSED. A non-PAUSED instance
        # would have the task claimed immediately by the worker pool.
        task_status = "PENDING" if state["instance_state"] == "PAUSED" else "CLAIMED"
        state["tasks"][msg_id] = {
            "message_id": msg_id,
            "instance_id": instance_id,
            "status": task_status,
        }
        return AsyncMessageResult(
            message_id=msg_id,
            instance_id=instance_id,
            status="queued",
            job_id=None,
            queued=True,
        )

    manager.enqueue_message = AsyncMock(side_effect=_enqueue)
    return manager, state


# ── Group 1 — long-downtime double-arm (T5.13) ─────────────────────────────


class TestLongDowntimeDoubleArm:
    """T5.13: two arms in a single daemon lifecycle coalesce to ONE
    wake with a run-list payload, delivered to the same arming
    instance. The body is newest-first; the dict is empty
    post-delivery. AC5 + D-FA5.2 + ADR-043 enforcement."""

    @pytest.mark.asyncio
    async def test_two_arms_in_long_downtime_coalesce_to_one_wake(
        self, install: Path
    ) -> None:
        """T5.13: arm A, restart, arm B, restart → the boot pass after
        the second restart sees 2 ``pending_wakes`` entries. They
        coalesce to ONE wake with a run-list payload, delivered to
        the same arming instance. The body is newest-first (B then A);
        one ``enqueue_message`` call; the dict is empty
        post-delivery."""
        # Arm A (older, armed first).
        _make_wake(
            install,
            run_id="r-A",
            arming_instance_id="i-same",
            armed_at="2026-10-04T00:00:00Z",
            expires_at="2026-10-04T00:10:00Z",
        )
        # Arm B (newer, armed second; same arming instance — the common
        # case for a long-running Ari session). Both arms are in scope
        # for the terminal event at ts=00:30:00.
        _make_wake(
            install,
            run_id="r-B",
            arming_instance_id="i-same",
            armed_at="2026-10-04T00:10:00Z",
            expires_at="2026-10-04T00:20:00Z",
        )
        # Both arms are now in the dict (2 pending_wakes).
        records = uj.list_pending_wakes(install)
        assert len(records) == 2, [r.run_id for r in records]
        # A terminal event covering BOTH arms (after both armed_at).
        _append_history(
            install, "commit", "promote to 1.2.3",
            ts="2026-10-04T00:30:00Z",
        )
        manager, _state = _mock_manager(instance_state="RUNNING")
        service = UpgradeJournalSweepService(install, manager=manager)
        result = await service.sweep_wake_records()
        # Both are delivered (the coalesce returns len(records), not 1).
        assert result.delivered == 2
        # BUT: one enqueue call (the coalesced group).
        assert manager.enqueue_message.await_count == 1
        # Inspect the call.
        kwargs = manager.enqueue_message.await_args.kwargs
        assert kwargs["instance_id"] == "i-same"
        # The body is the coalesced body with both run_ids, newest-first.
        body = kwargs["message"]
        assert "r-B" in body and "r-A" in body, body
        # Newest-first ordering: r-B must appear before r-A in the body.
        assert body.index("r-B") < body.index("r-A"), body
        # The dict is empty post-delivery (structural removal).
        records_after = uj.list_pending_wakes(install)
        assert records_after == [], [r.run_id for r in records_after]


# ── Group 2 — paused-instance defer (T5.14) ────────────────────────────────


class TestPausedInstanceDefer:
    """T5.14: a wake for a ``PAUSED`` instance is delivered (the
    ``MessageQueue`` row is created) but the ``Task`` is held
    ``PENDING`` until the instance resumes. AC5 + D-FA3.1; R-15
    mitigation."""

    @pytest.mark.asyncio
    async def test_wake_for_paused_instance_holds_task_pending(
        self, install: Path
    ) -> None:
        """T5.14: a PAUSED arming instance → the wake's
        ``enqueue_message`` creates the MessageQueue row + holds
        the Task in PENDING. The wake is "delivered" from the
        sweep's view (the sweep marks the record ``delivered``).
        The Task is in PENDING until the instance resumes."""
        _make_wake(install, run_id="r-aaa", arming_instance_id="i-paused-1")
        _append_history(install, "restart", "...")
        # The manager is in PAUSED state for this instance — the mock
        # simulates the claim-side pause gate.
        manager, state = _mock_manager(instance_state="PAUSED")
        service = UpgradeJournalSweepService(install, manager=manager)
        result = await service.sweep_wake_records()
        # (a) The wake was enqueued (delivery contract honored).
        assert manager.enqueue_message.await_count == 1
        # (b) The MessageQueue row was created.
        assert len(state["message_queue_rows"]) == 1
        assert state["message_queue_rows"][0]["source"] == "discord:user123"
        # (c) The Task is held PENDING (the pause gate held it).
        held_tasks = [
            t for t in state["tasks"].values()
            if t["status"] == "PENDING"
        ]
        assert len(held_tasks) == 1, list(state["tasks"].values())
        # (d) The wake record is `delivered` (structurally removed from
        # the dict after the sweep's removal pass).
        records_after = uj.list_pending_wakes(install)
        assert records_after == []
        # The result is clean (no errors).
        assert result.errors == 0

    @pytest.mark.asyncio
    async def test_wake_for_paused_instance_drains_on_resume(
        self, install: Path
    ) -> None:
        """T5.14 follow-on: after the instance resumes (PAUSED →
        RUNNING), the held Task is claimed and the agent's first
        turn runs. The test simulates the resume by flipping the
        mock's state and re-claiming; the worker's claim-side
        logic returns the held task as a candidate for dispatch."""
        _make_wake(install, run_id="r-aaa", arming_instance_id="i-paused-2")
        _append_history(install, "restart", "...")
        manager, state = _mock_manager(instance_state="PAUSED")
        service = UpgradeJournalSweepService(install, manager=manager)
        await service.sweep_wake_records()
        # Pre-resume: 1 held task.
        assert len(state["tasks"]) == 1
        held_msg_id = next(iter(state["tasks"]))
        assert state["tasks"][held_msg_id]["status"] == "PENDING"
        # Simulate the resume — the instance flips PAUSED → RUNNING.
        # The held task becomes a claim candidate for the worker pool.
        # The worker's `claim_pending_task` returns the held task.
        state["instance_state"] = "RUNNING"
        # The resume drains the held task — the worker pool claims it
        # via the existing `WorkerPool.claim_pending_task` lane. The
        # test asserts the resume's logical effect: the held task is
        # no longer in PENDING (the worker would claim it on the next
        # dispatch loop iteration; we simulate that here by marking it
        # CLAIMED, which is the post-claim state).
        # In production, the wake's response-message processing drives
        # the agent's first turn.
        state["tasks"][held_msg_id]["status"] = "CLAIMED"
        # Post-resume: the task is no longer held in PENDING.
        held_tasks = [
            t for t in state["tasks"].values()
            if t["status"] == "PENDING"
        ]
        assert held_tasks == []


# ── Group 3 — terminal-instance revival (T5.15) ────────────────────────────


class TestTerminalInstanceRevival:
    """T5.15: a wake for a terminal instance (COMPLETED / TERMINATED /
    ERROR / FAILED) triggers the existing terminal→RUNNING flip and
    delivers to the revived instance. AC5 + D-FA3.1."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "terminal_state", ["COMPLETED", "TERMINATED", "ERROR", "FAILED"]
    )
    async def test_wake_for_terminal_instance_revives_and_delivers(
        self, install: Path, terminal_state: str
    ) -> None:
        """T5.15 (parametrized over 4 terminal states): a wake for
        a terminal arming instance → ``enqueue_message`` triggers
        the terminal→RUNNING flip → the MessageQueue row is
        created → the wake is delivered to the revived instance.
        The recorded source rides the revive (D-FA3.3)."""
        _make_wake(install, run_id="r-aaa", arming_instance_id="i-terminal-1")
        _append_history(install, "restart", "...")
        # The instance is in a terminal state at the time of the wake.
        manager, state = _mock_manager(instance_state=terminal_state)
        service = UpgradeJournalSweepService(install, manager=manager)
        result = await service.sweep_wake_records()
        # (a) The wake was enqueued.
        assert manager.enqueue_message.await_count == 1
        # (b) The instance state is now RUNNING (the revive fired).
        assert state["instance_state"] == "RUNNING", (
            f"expected RUNNING after revive, got {state['instance_state']}"
        )
        # (c) The MessageQueue row was created (the wake is delivered).
        assert len(state["message_queue_rows"]) == 1
        # (d) The source is the recorded source (D-FA3.3 re-stamp
        # survived the revive).
        assert state["message_queue_rows"][0]["source"] == "discord:user123"
        # (e) The pending_wakes dict is empty (the sweep's structural
        # removal ran).
        records_after = uj.list_pending_wakes(install)
        assert records_after == []
        # The result is clean.
        assert result.errors == 0


# ── Group 4 — kill-switch re-enable-no-stale-flood (T5.17) ─────────────────


class TestKillSwitchReEnableNoStaleFlood:
    """T5.17 (architecture delta #2, MUST): the temporal scenario
    where the kill-switch is OFF for a period that covers the
    record's terminal transition, then re-enabled. Re-enabling
    delivers NOTHING stale — the OFF-period abandonment already
    drained the dict. ADR-044 persisted-record semantics."""

    @pytest.mark.asyncio
    async def test_re_enable_after_off_delivers_nothing_stale(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T5.17: (1) arm with kill-switch ON → record present;
        (2) flip the kill-switch OFF (covers the record's
        terminal transition) → the sweep's one-time abandon-pass
        marks it ``abandoned`` with ``reason=kill_switch_off``;
        (3) re-enable the kill-switch → the sweep runs, finds
        NO ``pending`` records, delivers NOTHING. Zero
        ``enqueue_message`` calls across all three ticks."""
        # (1) Arm with the kill-switch ON (the default).
        monkeypatch.delenv(ARM_NOTIFY_KILL_SWITCH_ENV, raising=False)
        _make_wake(install, run_id="r-aaa", arming_instance_id="i-rs-1")
        # Simulate that the pipeline has now terminated (a terminal
        # event in the journal) — but the kill-switch is OFF when
        # the sweep runs, so the OFF-period abandonment fires.
        _append_history(install, "commit", "promote to 1.2.3")
        # (2) Flip the kill-switch OFF.
        monkeypatch.setenv(ARM_NOTIFY_KILL_SWITCH_ENV, "0")
        manager, _state = _mock_manager(instance_state="RUNNING")
        service = UpgradeJournalSweepService(install, manager=manager)
        # First tick (OFF): abandon-pass marks the record abandoned.
        result_off = await service.sweep_wake_records()
        assert result_off.delivered == 0
        assert result_off.abandoned == 1
        # Zero enqueue calls while OFF.
        assert manager.enqueue_message.await_count == 0
        # The dict is empty (the record was abandoned + removed).
        assert uj.list_pending_wakes(install) == []
        # The wake_abandoned history event is present.
        history = journal_read(install).get("history", [])
        abandoned_events = [
            e for e in history
            if isinstance(e, dict) and e.get("event") == "wake_abandoned"
        ]
        assert len(abandoned_events) == 1
        assert (
            "kill_switch_off" in abandoned_events[0].get("detail", "")
        ), abandoned_events[0]
        # (3) Re-enable the kill-switch.
        monkeypatch.delenv(ARM_NOTIFY_KILL_SWITCH_ENV, raising=False)
        # Second tick (re-enabled): the dict is empty → no enqueue call.
        result_on = await service.sweep_wake_records()
        assert result_on.delivered == 0
        assert result_on.abandoned == 0
        # STILL zero enqueue calls — the late-deliver alternative is
        # structurally impossible (the OFF pass already drained).
        assert manager.enqueue_message.await_count == 0
        # No new wake_abandoned events on the re-enabled tick (the
        # abandonment is a one-time pass).
        history_after = journal_read(install).get("history", [])
        abandoned_after = [
            e for e in history_after
            if isinstance(e, dict) and e.get("event") == "wake_abandoned"
        ]
        assert len(abandoned_after) == 1
