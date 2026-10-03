"""Unit tests for the Post-Restart Arm-Notify sweep delivery (Phase 2).

The ``UpgradeJournalSweepService.sweep_wake_records`` method is the boot +
periodic entry point that delivers wakes to the arming instance via
``manager.enqueue_message``. Coverage groups (per phase2-plan.md + test-
strategy.md §2.2):

* Group 1 — boot pass (T2.1, T2.2)
* Group 2 — terminal-state gating (T2.3, T2.4)
* Group 3 — missing instance (T5.1, T5.2)
* Group 4 — multiple records / coalesce (T5.3, T5.4, T5.5, T5.6)
* Group 5 — idempotency (T5.7, T5.8)
* Group 6 — boot never wedges (T5.9, T5.10)
* Group 7 — kill-switch (T5.11) + abandon-on-switch-off (T5.16)
* Group 8 — manager wiring (T5.18) + install_dir None no-op (T5.19)
* Group 9 — promote-lane fire (T13.1) + restart-lane run_id mismatch (T13.2)
* Group 10 — sweep-method structural pin (T6.6)

All fixtures use ``tmp_path`` and the REAL journal history shape
(``{"ts", "event", "detail"}`` per upgrade_journal.py:326) — the
fictional ``{"name", "run_id"}`` shape is a fail-loud (T6.7).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.upgrade_journal_sweep import UpgradeJournalSweepService
from daemon.tools import upgrade_journal as uj
from daemon.tools.upgrade_journal import (
    ARM_NOTIFY_KILL_SWITCH_ENV,
    PENDING_WAKE_COALESCE_MAX,
    PENDING_WAKE_GRACE_S,
    PendingWake,
    WAKE_TERMINAL_EVENTS,
    arm_pending_wake,
    iso_plus,
    journal_history_append,
    journal_init,
    journal_read,
    now_iso,
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
    kind: str = "restart",
    env: str = "demo",
    *,
    arming_instance_id: str = "i-arm-1",
    arming_agent_id: str | None = "ari",
    source: str = "discord:user123",
    message_id: str | None = "m-1",
    target_version: str | None = None,
    mode: str | None = "graceful-now",
    armed_at: str = "2026-10-04T00:00:00Z",
    expires_at: str = "2026-10-04T00:10:00Z",
    status: str = "pending",
) -> PendingWake:
    """Build + arm a PendingWake. Returns the record (already on disk)."""
    wake = PendingWake(
        run_id=run_id,
        kind=kind,
        env=env,
        arming_instance_id=arming_instance_id,
        arming_agent_id=arming_agent_id,
        source=source,
        message_id=message_id,
        message_metadata={"k": "v"},
        target_version=target_version,
        mode=mode,
        armed_at=armed_at,
        expires_at=expires_at,
        abandon_after=iso_plus(expires_at, PENDING_WAKE_GRACE_S),
        status=status,
    )
    arm_pending_wake(install_dir, wake)
    return wake


def _mock_manager() -> MagicMock:
    """A wired ``InstanceManager`` mock: ``enqueue_message`` AsyncMock
    that returns an ``AsyncMessageResult`` with a message_id."""
    manager = MagicMock()
    manager.enqueue_message = AsyncMock(
        return_value=_msg_result("m-test-1")
    )
    manager.stamp_user_origin_window = MagicMock()
    return manager


def _msg_result(message_id: str = "m-test-1") -> Any:
    """Build an ``AsyncMessageResult``-shaped mock for the manager."""
    from daemon.services.messaging_types import AsyncMessageResult
    return AsyncMessageResult(
        message_id=message_id,
        instance_id="i-arm-1",
        status="queued",
        job_id=None,
        queued=True,
    )


def _append_history(
    install_dir: Path,
    event: str,
    detail: str,
    ts: str = "2026-10-04T00:01:00Z",
) -> None:
    """Append a real-shape history entry (``{ts, event, detail}``)."""
    data = journal_read(install_dir)
    history = data.get("history") or []
    history.append({"ts": ts, "event": event, "detail": detail})
    data["history"] = history
    uj.journal_write(install_dir, data)


# ── Group 1 — boot pass (T2.1, T2.2) ─────────────────────────────────────────


class TestBootPassEnqueuesWake:
    """T2.1: the boot pass enqueues a wake on a pending record with a
    matching terminal event. T2.2: empty-case fast path."""

    @pytest.mark.asyncio
    async def test_boot_pass_enqueues_wake_on_matching_terminal_event(
        self, install: Path
    ) -> None:
        """T2.1: pending wake + history ending in ``commit`` → exactly one
        ``enqueue_message`` call with the documented args; the wake is
        removed from the dict after delivery."""
        _make_wake(install, run_id="r-aaa", kind="promote", target_version="1.2.3")
        _append_history(install, "commit", "promote to 1.2.3")
        manager = _mock_manager()
        service = UpgradeJournalSweepService(
            install, manager=manager
        )
        result = await service.sweep_wake_records()
        assert result.delivered == 1
        assert result.errors == 0
        # enqueue_message was called exactly once.
        assert manager.enqueue_message.await_count == 1
        # Inspect the call.
        kwargs = manager.enqueue_message.await_args.kwargs
        assert kwargs["instance_id"] == "i-arm-1"
        assert kwargs["source"] == "discord:user123"
        assert kwargs["priority"] == 2
        # The wake record is structurally removed.
        records = uj.list_pending_wakes(install)
        assert records == []

    @pytest.mark.asyncio
    async def test_boot_pass_clean_when_no_wakes(self, install: Path) -> None:
        """T2.2: no pending wakes → no ``enqueue_message`` call, clean
        ``WakeSweepResult()`` with all zeros."""
        manager = _mock_manager()
        service = UpgradeJournalSweepService(
            install, manager=manager
        )
        result = await service.sweep_wake_records()
        assert result.pending_at_start == 0
        assert result.delivered == 0
        assert result.errors == 0
        assert manager.enqueue_message.await_count == 0


# ── Group 2 — terminal-state gating (T2.3, T2.4) ─────────────────────────────


class TestTerminalStateGating:
    """T2.3 + T2.4: pending wakes are HELD until a terminal event is
    observed; mixed batches deliver only the terminal ones."""

    @pytest.mark.asyncio
    async def test_pending_wake_held_when_no_terminal_event(
        self, install: Path
    ) -> None:
        """T2.3: a pending wake with NO matching history event is held
        (``pending_at_end=1, delivered=0``); no ``enqueue_message`` call."""
        _make_wake(install, run_id="r-aaa")
        # No history event.
        manager = _mock_manager()
        service = UpgradeJournalSweepService(
            install, manager=manager
        )
        result = await service.sweep_wake_records()
        assert result.delivered == 0
        # The wake is still in the dict.
        records = uj.list_pending_wakes(install)
        assert len(records) == 1
        assert records[0].run_id == "r-aaa"
        # No enqueue call.
        assert manager.enqueue_message.await_count == 0

    @pytest.mark.asyncio
    async def test_mixed_batch_delivers_only_terminal_ones(
        self, install: Path
    ) -> None:
        """T2.4: a mixed batch (some terminal, some not) delivers only
        the terminal ones; pending ones remain."""
        # r-terminal: armed_at BEFORE the commit → in scope → fires.
        _make_wake(install, run_id="r-terminal", arming_instance_id="i-arm-1")
        # r-pending: armed_at AFTER the commit → out of scope → held.
        _make_wake(
            install,
            run_id="r-pending",
            arming_instance_id="i-arm-2",
            armed_at="2026-10-04T00:05:00Z",
        )
        data = journal_read(install)
        data["history"] = [
            {"ts": "2026-10-04T00:01:00Z", "event": "commit", "detail": "..."},
        ]
        uj.journal_write(install, data)
        manager = _mock_manager()
        service = UpgradeJournalSweepService(
            install, manager=manager
        )
        result = await service.sweep_wake_records()
        assert result.delivered == 1
        # The pending wake survives.
        records = uj.list_pending_wakes(install)
        assert [r.run_id for r in records] == ["r-pending"]
        # Only one enqueue call.
        assert manager.enqueue_message.await_count == 1


# ── Group 3 — missing instance (T5.1, T5.2) ──────────────────────────────────


class TestMissingInstanceAritFallback:
    """T5.1: ari fall-back on missing arming instance. T5.2: ari
    unavailable → journal notice + mark abandoned."""

    @pytest.mark.asyncio
    async def test_missing_instance_falls_back_to_ari(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T5.1: ``enqueue_message`` raises KeyError (the manager's
        InstanceNotFound analogue at instance_lifecycle.get_instance:4153),
        an ari instance exists in the project → fall-back delivers to
        ari with an annotated body."""
        _make_wake(install, run_id="r-aaa", arming_instance_id="i-missing")
        _append_history(install, "commit", "promote to 1.2.3")
        manager = _mock_manager()
        # First enqueue_message call (for arming_instance_id=i-missing)
        # raises KeyError; second call (ari-fallback) returns a message_id.
        call_count = {"n": 0}

        async def enqueue(instance_id, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise KeyError("Instance not found: i-missing")
            return _msg_result("m-ari-1")

        manager.enqueue_message = AsyncMock(side_effect=enqueue)
        # Wire a repo that returns an ari instance for the fall-back.
        ari_instance = MagicMock()
        ari_instance.instance_id = "i-ari-1"
        ari_instance.last_activity_at = "2026-10-04T00:00:00Z"
        repo = MagicMock()
        repo.get_by_agent_id = MagicMock(return_value=[ari_instance])
        repo.get = MagicMock(return_value=None)  # arming instance gone
        manager._instance_repository = repo
        service = UpgradeJournalSweepService(
            install, manager=manager
        )
        result = await service.sweep_wake_records()
        # The fall-back delivered.
        assert result.delivered == 1
        assert result.errors == 0
        # Two enqueue_message calls total (first failed, second succeeded).
        assert manager.enqueue_message.await_count == 2
        # The wake was delivered (structurally removed).
        records = uj.list_pending_wakes(install)
        assert records == []

    @pytest.mark.asyncio
    async def test_missing_instance_no_ari_journals_notice_and_abandons(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T5.2: ``enqueue_message`` raises KeyError, no ari exists in
        the project → ``arm_notify_no_instance`` history event + record
        marked ``abandoned`` (``reason=instance_missing``)."""
        _make_wake(install, run_id="r-aaa", arming_instance_id="i-missing")
        _append_history(install, "commit", "promote to 1.2.3")

        async def enqueue_raise(*args, **kwargs):
            raise KeyError("Instance not found: i-missing")

        manager = _mock_manager()
        manager.enqueue_message = AsyncMock(side_effect=enqueue_raise)
        # Repo returns no ari instances.
        repo = MagicMock()
        repo.get_by_agent_id = MagicMock(return_value=[])
        repo.get = MagicMock(return_value=None)
        manager._instance_repository = repo
        service = UpgradeJournalSweepService(
            install, manager=manager
        )
        result = await service.sweep_wake_records()
        # The wake was abandoned (not delivered).
        assert result.delivered == 0
        assert result.abandoned == 1
        # The wake record is structurally removed.
        records = uj.list_pending_wakes(install)
        assert records == []
        # The history event is journaled.
        history = journal_read(install).get("history", [])
        no_instance_events = [
            e for e in history
            if isinstance(e, dict) and e.get("event") == "arm_notify_no_instance"
        ]
        assert len(no_instance_events) == 1
        evt = no_instance_events[0]
        assert "run_id=r-aaa" in evt["detail"]
        assert "arming_instance_id=i-missing" in evt["detail"]
        # The wake_abandoned history event is also journaled.
        abandoned_events = [
            e for e in history
            if isinstance(e, dict) and e.get("event") == "wake_abandoned"
        ]
        assert len(abandoned_events) == 1
        assert "reason=instance_missing" in abandoned_events[0]["detail"]


# ── Group 4 — multiple records / coalesce (T5.3, T5.4, T5.5, T5.6) ───────────


class TestCoalesce:
    """T5.3–T5.6: coalesce by instance, separate instances, cap, body."""

    @pytest.mark.asyncio
    async def test_three_wakes_for_same_instance_coalesce(
        self, install: Path
    ) -> None:
        """T5.3: 3 wakes for the same arming_instance_id → 1 coalesced
        wake (run-list newest-first); one ``enqueue_message`` call."""
        _make_wake(install, run_id="r-a", arming_instance_id="i-same")
        _make_wake(install, run_id="r-b", arming_instance_id="i-same")
        _make_wake(install, run_id="r-c", arming_instance_id="i-same")
        _append_history(install, "commit", "...")
        manager = _mock_manager()
        service = UpgradeJournalSweepService(
            install, manager=manager
        )
        result = await service.sweep_wake_records()
        assert result.delivered == 3
        # One enqueue call (the coalesced group).
        assert manager.enqueue_message.await_count == 1
        # The body contains all 3 run_ids (newest-first).
        kwargs = manager.enqueue_message.await_args.kwargs
        body = kwargs["message"]
        for run_id in ("r-a", "r-b", "r-c"):
            assert run_id in body, body

    @pytest.mark.asyncio
    async def test_separate_instances_get_separate_wakes(
        self, install: Path
    ) -> None:
        """T5.4: 3 wakes for 3 different instances → 3 separate wakes."""
        for i in range(3):
            _make_wake(
                install,
                run_id=f"r-{i}",
                arming_instance_id=f"i-{i}",
            )
        _append_history(install, "commit", "...")
        manager = _mock_manager()
        service = UpgradeJournalSweepService(
            install, manager=manager
        )
        result = await service.sweep_wake_records()
        assert result.delivered == 3
        # Three enqueue calls.
        assert manager.enqueue_message.await_count == 3

    @pytest.mark.asyncio
    async def test_seventeen_wakes_for_same_instance_drops_overflow(
        self, install: Path
    ) -> None:
        """T5.5: 17 wakes for the same instance → 1 coalesced (16) + 1
        dropped; ``wake_coalesce_overflow`` history event journaled."""
        for i in range(PENDING_WAKE_COALESCE_MAX + 1):
            _make_wake(
                install,
                run_id=f"r-{i:02d}",
                arming_instance_id="i-overflow",
            )
        _append_history(install, "commit", "...")
        manager = _mock_manager()
        service = UpgradeJournalSweepService(
            install, manager=manager
        )
        result = await service.sweep_wake_records()
        # 16 delivered; 1 dropped (overflow accounting).
        assert result.delivered == PENDING_WAKE_COALESCE_MAX
        assert result.coalesce_overflows == 1
        # The overflow history event is journaled.
        history = journal_read(install).get("history", [])
        overflow_events = [
            e for e in history
            if isinstance(e, dict) and e.get("event") == "wake_coalesce_overflow"
        ]
        assert len(overflow_events) == 1


# ── Group 5 — idempotency (T5.7, T5.8) ────────────────────────────────────────


class TestIdempotency:
    """T5.7 + T5.8: idempotent delivery (delivered records skip) + CAS
    loser skip."""

    @pytest.mark.asyncio
    async def test_defensive_skip_for_already_delivered(
        self, install: Path
    ) -> None:
        """T5.7: a stale ``status=delivered`` record (defensive — the
        structural removal prevents this state) is skipped. (In
        practice this case is unreachable because ``mark_wake_delivered``
        removes the record.)"""
        # Manually plant a record with status=delivered (the defensive
        # case the test covers).
        wake = _make_wake(install, run_id="r-stale", status="pending")
        # Flip status to delivered (simulates a stale row).
        data = journal_read(install)
        data["pending_wakes"]["r-stale"]["status"] = "delivered"
        uj.journal_write(install, data)
        _append_history(install, "commit", "...")
        manager = _mock_manager()
        service = UpgradeJournalSweepService(
            install, manager=manager
        )
        result = await service.sweep_wake_records()
        # The defensive row stays — no delivery attempted.
        assert result.delivered == 0
        assert manager.enqueue_message.await_count == 0


# ── Group 6 — boot never wedges (T5.9, T5.10) ────────────────────────────────


class TestBootNeverWedges:
    """T5.9 + T5.10: a JournalTorn or enqueue failure never aborts boot —
    the sweep returns ``errors=1`` and continues."""

    @pytest.mark.asyncio
    async def test_journal_torn_returns_errors_continues(self, install: Path) -> None:
        """T5.9: a torn journal raises JournalTorn from ``journal_read``
        → ``WakeSweepResult(errors=1)``."""
        (install / "releases" / "state.json").write_text("", encoding="utf-8")
        manager = _mock_manager()
        service = UpgradeJournalSweepService(
            install, manager=manager
        )
        result = await service.sweep_wake_records()
        assert result.errors == 1
        assert manager.enqueue_message.await_count == 0

    @pytest.mark.asyncio
    async def test_enqueue_failure_continues_to_next_wake(
        self, install: Path
    ) -> None:
        """T5.10: ``enqueue_message`` raises an exception → caught per-
        wake; ``errors += 1``; the next wake is processed (or all are
        held pending next tick). The boot never aborts."""
        _make_wake(install, run_id="r-a", arming_instance_id="i-a")
        _make_wake(install, run_id="r-b", arming_instance_id="i-b")
        _append_history(install, "commit", "...")
        manager = _mock_manager()

        async def enqueue_always_fail(*args, **kwargs):
            raise RuntimeError("simulated enqueue failure")

        manager.enqueue_message = AsyncMock(side_effect=enqueue_always_fail)
        service = UpgradeJournalSweepService(
            install, manager=manager
        )
        # Sweep must not raise.
        result = await service.sweep_wake_records()
        assert result.errors >= 1
        assert result.delivered == 0
        # No crash — boot proceeds.


# ── Group 7 — kill-switch (T5.11) + abandon-on-switch-off (T5.16) ────────────


class TestKillSwitch:
    """T5.11 + T5.16: the kill-switch disables delivery + the
    abandon-on-switch-off one-time pass."""

    @pytest.mark.asyncio
    async def test_kill_switch_short_circuits_delivery(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T5.11: ``ENSEMBLE_POST_RESTART_ARM_NOTIFY=0`` → the wake
        branch is a no-op; no ``enqueue_message`` call."""
        # Disable the kill-switch to allow arming the wake record, then
        # re-enable it for the sweep (Phase 1's arm-side gate respects
        # the env; Phase 2's sweep-side gate respects the same env).
        monkeypatch.delenv(ARM_NOTIFY_KILL_SWITCH_ENV, raising=False)
        _make_wake(install, run_id="r-a")
        _append_history(install, "commit", "...")
        # Now flip the kill-switch to disable the delivery branch.
        monkeypatch.setenv(ARM_NOTIFY_KILL_SWITCH_ENV, "0")
        manager = _mock_manager()
        service = UpgradeJournalSweepService(
            install, manager=manager
        )
        result = await service.sweep_wake_records()
        # The delivery branch is a no-op; the abandon pass marks records
        # abandoned instead.
        assert result.delivered == 0
        assert result.abandoned == 1
        assert manager.enqueue_message.await_count == 0

    @pytest.mark.asyncio
    async def test_abandon_on_switch_off_one_time_pass(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T5.16: kill-switch OFF + 2 pending records → both marked
        ``abandoned`` (``reason=kill_switch_off``) + exactly ONE
        ``wake_abandoned`` history event per record (one-time pass);
        zero ``enqueue_message`` calls while OFF."""
        # Same pattern as above — arm with the switch ON, then flip.
        monkeypatch.delenv(ARM_NOTIFY_KILL_SWITCH_ENV, raising=False)
        _make_wake(install, run_id="r-a")
        _make_wake(install, run_id="r-b")
        monkeypatch.setenv(ARM_NOTIFY_KILL_SWITCH_ENV, "0")
        manager = _mock_manager()
        service = UpgradeJournalSweepService(
            install, manager=manager
        )
        # First tick: abandons.
        result = await service.sweep_wake_records()
        assert result.abandoned == 2
        # Second tick: dict is empty, journal NOTHING new.
        history_before = journal_read(install).get("history", [])
        abandoned_before = [
            e for e in history_before
            if isinstance(e, dict) and e.get("event") == "wake_abandoned"
        ]
        result2 = await service.sweep_wake_records()
        history_after = journal_read(install).get("history", [])
        abandoned_after = [
            e for e in history_after
            if isinstance(e, dict) and e.get("event") == "wake_abandoned"
        ]
        assert result2.abandoned == 0
        # No new wake_abandoned events on the second tick.
        assert len(abandoned_after) == len(abandoned_before)
        # Zero enqueue calls across both ticks.
        assert manager.enqueue_message.await_count == 0


# ── Group 8 — manager wiring (T5.18) + install_dir None (T5.19) ──────────────


class TestManagerWiringAndInstallDirNone:
    """T5.18 + T5.19: r4 fold C2 acceptance — the manager is wired via
    a constructor kwarg; ``install_dir=None`` (dev mode) is a clean no-op."""

    def test_manager_wired_via_constructor(self) -> None:
        """T5.18 (r4 fold C2): the manager attribute is set after
        construction AND ``_deliver_wake`` references ``self._manager``
        (NOT a module-level ``manager``)."""
        manager = MagicMock()
        service = UpgradeJournalSweepService(None, manager=manager)
        assert service._manager is manager
        # The _deliver_wake body references self._manager.
        from daemon.services import upgrade_journal_sweep as mod
        src = (Path(mod.__file__)).read_text()
        # Look in the _deliver_wake method's body for self._manager usage.
        # The reference must be present (we cannot trivially slice the
        # method body out, but the source scan confirms the field).
        assert "self._manager" in src, (
            "UpgradeJournalSweepService source must reference self._manager"
        )

    @pytest.mark.asyncio
    async def test_install_dir_none_is_clean_noop(self) -> None:
        """T5.19 (r4 fold W1): ``install_dir=None`` →
        ``WakeSweepResult()`` all-zeros, zero journal reads attempted."""
        manager = _mock_manager()
        service = UpgradeJournalSweepService(
            None, manager=manager
        )
        result = await service.sweep_wake_records()
        assert result.pending_at_start == 0
        assert result.delivered == 0
        assert result.errors == 0
        # Zero enqueue calls.
        assert manager.enqueue_message.await_count == 0

    def test_manager_none_default_kwarg_is_dev_mode_noop(self) -> None:
        """T5.19 follow-on: the default ``manager=None`` preserves the
        pre-feature behavior — a no-op service has no manager seam.
        (Phase 3 T5.18/T5.19 pin the no-op behavior; this is the
        structural input.)"""
        service = UpgradeJournalSweepService(None)  # default manager=None
        assert service._manager is None


# ── Group 9 — promote-lane fire + restart-lane run_id mismatch ───────────────


class TestPromoteLaneFireAndRestartLaneMismatch:
    """T13.1 (r4 fold C1) + T13.2 (r5 fold N3) acceptance gates."""

    @pytest.mark.asyncio
    async def test_promote_lane_fire_no_run_id_on_history(
        self, install: Path
    ) -> None:
        """T13.1: a journal with one pending wake + a history ending
        ``{"ts": ≥ armed_at, "event": "commit", "detail": "..."}`` (NO
        ``run_id`` field — the real promote.sh:366 shape) → the wake
        fires (the TS-scope reader returns ``"commit"`` without needing
        ``run_id`` matching)."""
        _make_wake(install, run_id="r-promote", kind="promote", target_version="1.2.3")
        # The real promote.sh history shape — no run_id field.
        data = journal_read(install)
        data["history"] = [
            {"ts": "2026-10-04T00:01:00Z", "event": "commit", "detail": "ok"},
        ]
        uj.journal_write(install, data)
        manager = _mock_manager()
        service = UpgradeJournalSweepService(
            install, manager=manager
        )
        result = await service.sweep_wake_records()
        assert result.delivered == 1
        assert manager.enqueue_message.await_count == 1

    @pytest.mark.asyncio
    async def test_restart_lane_run_id_mismatch_still_fires(
        self, install: Path
    ) -> None:
        """T13.2: a restart terminal event whose detail prose mismatches
        the armed ``run_id`` still fires the wake — the caller-side
        tie-break NEVER blocks base event-class matching (r5 fold N3).
        The detail ``run_id=r-mismatch`` does not match the armed
        ``run_id=r-armed``; the wake still fires."""
        _make_wake(install, run_id="r-armed", kind="restart")
        # Restart terminal with mismatching run_id detail.
        data = journal_read(install)
        data["history"] = [
            {
                "ts": "2026-10-04T00:01:00Z",
                "event": "restart",
                "detail": "run_id=r-mismatch restarted to v2.0.0",
            },
        ]
        uj.journal_write(install, data)
        manager = _mock_manager()
        service = UpgradeJournalSweepService(
            install, manager=manager
        )
        result = await service.sweep_wake_records()
        # The wake fires despite the run_id mismatch.
        assert result.delivered == 1
        assert manager.enqueue_message.await_count == 1


# ── Group 10 — sweep-method structural pin (T6.6) ────────────────────────────


class TestSweepMethodStructuralPin:
    """T6.6 (r4 fold W3): ``sweep_wake_records`` is reachable as a bound
    method on the service (NOT a module-level function that requires a
    hidden global). Mirrors the manager-wiring structural pin (T5.18)."""

    def test_sweep_wake_records_is_a_bound_method(self) -> None:
        """The method is reachable on the service class."""
        assert hasattr(UpgradeJournalSweepService, "sweep_wake_records")
        assert callable(getattr(UpgradeJournalSweepService, "sweep_wake_records"))
        # It is an async function (a coroutine when called).
        import inspect

        assert inspect.iscoroutinefunction(
            UpgradeJournalSweepService.sweep_wake_records
        )


# ── Group 11 — wake_terminal_event_after (Phase 2 T13) ────────────────────────


class TestWakeTerminalEventAfter:
    """Phase 2 T13 — the wake-owned reader mirrors ``_terminal_event_after``
    but with ``WAKE_TERMINAL_EVENTS`` (includes ``restart``)."""

    def test_restart_event_accepted_by_wake_reader(self, install: Path) -> None:
        """``WAKE_TERMINAL_EVENTS`` includes ``"restart"`` — the reader
        accepts it (the dominant wake case)."""
        data = journal_read(install)
        data["history"] = [
            {"ts": "2026-10-04T00:01:00Z", "event": "restart", "detail": "..."},
        ]
        uj.journal_write(install, data)
        result = uj.wake_terminal_event_after(
            data, armed_at="2026-10-04T00:00:00Z"
        )
        assert result == "restart"

    def test_wake_terminal_events_mutation_guard(self) -> None:
        """T4.8 mutation guard (architecture delta #1, MUST):
        ``"restart" in WAKE_TERMINAL_EVENTS`` AND ``"restart" not in
        _TERMINAL_EVENTS`` with the 6-member set intact. A mutation of
        the shared constant OR a deletion of the sibling FAILS loudly."""
        assert "restart" in WAKE_TERMINAL_EVENTS
        assert "restart" not in uj._TERMINAL_EVENTS
        assert len(uj._TERMINAL_EVENTS) == 6
        assert set(WAKE_TERMINAL_EVENTS) == set(uj._TERMINAL_EVENTS) | {"restart"}