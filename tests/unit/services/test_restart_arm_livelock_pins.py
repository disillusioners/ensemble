"""Regression pins for the restart-arm livelock fix (incident 2026-10-31,
commission wi-6641579b).

THE INCIDENT SHAPE (all seeded on scratch tmp dirs — never a live
install): a restart arm mints ``in_flight {kind=restart, flipped=false,
owner_pid=<live daemon>}`` + ``pending_op`` + takes the journal lock in
the daemon's own name; the shell executor is then KILLED before
adopting it (``executor_exit exit_code=-1``, journaled by the reaper's
ChildProcessError leg). The daemon never restarted, so:

* the journal lock's owner pid is the LIVE daemon — ``lock_acquire``'s
  stale-break ("heartbeat > 300s AND owner dead" / "owner dead") can
  NEVER fire;
* the reaper's ``executor_exit`` event is terminal-class for wakes
  (``_TERMINAL_EVENTS`` member), so every 90s sweep cycle fires the
  pending restart wake into ``mark_wake_delivering`` →
  ``lock_acquire(wait_s=30.0)`` — a SYNCHRONOUS wait on the event
  loop — against the run's own open txn;
* the CAS loses every cycle → the wake is never delivered, the txn is
  never closed → the arming lane sleeps forever = livelock until a
  human restarts the daemon (boot recovery's ``close_stale_restart_arm``
  being the only existing closer).

THE TWO-PART FIX UNDER PIN:

* PART 1 — ``uj.mark_wake_delivering``: probe the lock non-blocking
  FIRST; when the holder is THIS run's own open ``in_flight`` txn
  (run_id matches AND ``flipped`` not true), log + return ``None``
  IMMEDIATELY (retryable skip — the 30s wait loop is never entered for
  this class). Foreign holders and flipped=true keep the original
  wait-then-skip semantics.
* PART 2 — ``sweep._journal_executor_exit``: an OBSERVED executor exit
  on an unflipped restart txn closes the arm (``in_flight=None`` +
  ``clear_pending_op`` + ``restart_abandoned`` event — the boot lane's
  vocabulary — + run_id-matched lock release), so the NEXT sweep cycle
  delivers the wake. NEVER touches a flipped=true txn (mutation
  happened — halt-for-human semantics stay).

MUTATION DISCIPLINE: every pin here was proven RED under its planted
mutation and GREEN at the fix (M1 = identity check removed; M2 = closer
call removed; M3 = flipped guard removed). The 30s wait loop is NEVER
run for real: the pack swaps ``uj.time`` for a counting-shim whose
``sleep`` is a no-op, so the old code's 30-iteration burn is OBSERVED
as ``sleep_calls == 30`` instead of suffered as wall-clock.

Fixture shape follows ``tests/unit/services/test_post_restart_arm_notify_sweep.py``
(real journal shapes via ``uj`` primitives; tmp_path only; mock manager).
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.upgrade_journal_sweep import ReaperJob, UpgradeJournalSweepService
from daemon.tools import upgrade_journal as uj
from daemon.tools.upgrade_journal import (
    PENDING_WAKE_GRACE_S,
    PendingWake,
    arm_pending_wake,
    iso_plus,
    journal_init,
    journal_read,
    now_iso,
)

RUN_ID = "r-20261031-071535-d526"
ARMED_AT = "2026-10-04T00:00:00Z"  # static past — now-written events are in scope


# ── Fixtures + incident-seed helpers ─────────────────────────────────────────


@pytest.fixture
def install(tmp_path: Path) -> Path:
    """Fresh staged-install fixture (scratch only — never a live dir)."""
    inst = tmp_path / "install"
    (inst / "releases").mkdir(parents=True)
    journal_init(inst)
    uj.ensure_extensions(inst)
    return inst


class _FakeTime:
    """``time`` namespace shim for ``uj``: real wall clock for
    ``time()``, a VIRTUAL monotonic clock that advances 1.0s per
    ``sleep`` call. The 30s lock-wait loop therefore terminates after
    exactly 30 shim sleeps (observed as ``sleep_calls == 30``) without
    real wall-clock — the pack never sleeps 30s for real, and the
    wait-loop burn is a deterministic, countable discriminator."""

    def __init__(self) -> None:
        self.sleep_calls = 0
        self._base = time.monotonic()

    def sleep(self, _seconds: float) -> None:
        self.sleep_calls += 1

    def monotonic(self) -> float:
        return self._base + float(self.sleep_calls)

    def time(self) -> float:
        return time.time()


@pytest.fixture
def fake_uj_time(monkeypatch: pytest.MonkeyPatch) -> _FakeTime:
    shim = _FakeTime()
    monkeypatch.setattr(uj, "time", shim, raising=True)
    return shim


def _seed_incident_state(
    install: Path,
    *,
    run_id: str = RUN_ID,
    flipped: bool = False,
    with_wake: bool = True,
    with_executor_exit_event: bool = True,
) -> PendingWake | None:
    """Seed the journal EXACTLY as the 07:15 incident left it:

    * the journal lock HELD in the live daemon's own name (owner pid =
      this test process — alive, so stale-break can never fire);
    * ``in_flight`` restart txn, ``flipped`` as given, owner = own pid;
    * ``pending_op`` kind=restart (the arm envelope);
    * a PENDING wake for the run (future expiry — the grace pass must
      hold it, not abandon it);
    * optionally the ``executor_exit`` history event the reaper
      journaled (terminal-class for wakes — the incident's trigger
      that fires the wake into the CAS every cycle while closing
      nothing).
    """
    acquired, busy = uj.lock_acquire(install, run_id, owner_pid=os.getpid())
    assert acquired, f"seed failed: lock busy ({busy})"
    uj.journal_update_field(
        install,
        "in_flight",
        {
            "kind": "restart",
            "target": "0.28.0",
            "started_at": now_iso(),
            "flipped": flipped,
            "owner_pid": os.getpid(),
            "run_id": run_id,
        },
    )
    uj.write_pending_op(
        install,
        uj.PendingOp(
            run_id=run_id,
            kind="restart",
            env="live",
            target="0.28.0",
            mode="graceful-now",
            reason=".env reload",
            owner_pid=os.getpid(),
            owner_kind="tool-arm",
            expires_at=iso_plus(now_iso(), uj.PENDING_OP_EXPIRE_RESTART_S),
        ),
    )
    wake: PendingWake | None = None
    if with_wake:
        wake = _make_wake(install, run_id=run_id)
    if with_executor_exit_event:
        _append_history(
            install,
            "executor_exit",
            f"run_id={run_id} pid=198809 exit_code=-1 argv=['restart.sh'] "
            "upgrade.log tail: (killed pre-adoption)",
        )
    return wake


def _make_wake(
    install_dir: Path,
    run_id: str = RUN_ID,
    kind: str = "restart",
) -> PendingWake:
    """Arm a PENDING wake whose grace window is in the FUTURE (the
    grace pass must hold it — the incident wake was held, not graced)."""
    wake = PendingWake(
        run_id=run_id,
        kind=kind,
        env="live",
        arming_instance_id="i-arm-1",
        arming_agent_id="ari",
        source="discord:user123",
        message_id="m-1",
        message_metadata={"k": "v"},
        target_version=None,
        mode="graceful-now",
        armed_at=ARMED_AT,
        expires_at=iso_plus(now_iso(), 3600),
        abandon_after=iso_plus(now_iso(), 3600 + PENDING_WAKE_GRACE_S),
        status="pending",
    )
    arm_pending_wake(install_dir, wake)
    return wake


def _append_history(
    install_dir: Path,
    event: str,
    detail: str,
    ts: str | None = None,
) -> None:
    """Append a real-shape history entry (``{ts, event, detail}``)."""
    data = journal_read(install_dir)
    history = data.get("history") or []
    history.append({"ts": ts or now_iso(), "event": event, "detail": detail})
    data["history"] = history
    uj.journal_write(install_dir, data)


def _mock_manager() -> MagicMock:
    manager = MagicMock()
    manager.enqueue_message = AsyncMock(return_value=_msg_result("m-test-1"))
    manager.stamp_user_origin_window = MagicMock()
    return manager


def _msg_result(message_id: str = "m-test-1") -> Any:
    from daemon.services.messaging_types import AsyncMessageResult

    return AsyncMessageResult(
        message_id=message_id,
        instance_id="i-arm-1",
        status="queued",
        job_id=None,
        queued=True,
    )


def _reaper_job(install: Path, run_id: str = RUN_ID, pid: int = 198809) -> ReaperJob:
    return ReaperJob(pid=pid, argv_summary=("restart.sh",), install_dir=install, run_id=run_id)


# ── PART 1 — own-txn retryable skip (identity check BEFORE the 30s wait) ─────


class TestOwnTxnRetryableSkip:
    """The livelock's CAS leg: own-open-txn lock ⇒ immediate retryable
    skip — no 30s wait-loop burn, wake stays pending, lock untouched."""

    def test_mark_wake_delivering_skips_immediately_without_wait_burn(
        self, install: Path, fake_uj_time: _FakeTime, caplog: pytest.LogCaptureFixture
    ) -> None:
        """GREEN at the fix: ``None`` returned, ZERO wait-loop sleeps, the
        retryable-skip warning logged, wake still pending, lock STILL
        held (a skip must never release someone else's lock)."""
        _seed_incident_state(install)
        with caplog.at_level(logging.WARNING):
            result = uj.mark_wake_delivering(install, RUN_ID)
        assert result is None
        # THE discriminator: the old code entered the 30s wait loop
        # (30 × sleep(1)); the fix never enters it for this class.
        assert fake_uj_time.sleep_calls == 0
        assert "own open in_flight txn" in caplog.text
        assert "retryable skip" in caplog.text
        # The wake stays pending for the next cycle (retryable skip).
        records = uj.list_pending_wakes(install)
        assert len(records) == 1
        assert records[0].status == "pending"
        # The skip must not release the holder's lock.
        assert uj.lock_run_id(install) == RUN_ID

    @pytest.mark.asyncio
    async def test_sweep_tick_with_own_txn_lock_stays_clean_and_pending(
        self, install: Path, fake_uj_time: _FakeTime, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The full incident tick: executor_exit journaled (wake fires),
        lock held by the own open txn ⇒ the sweep cycle SKIPS cleanly —
        delivered=0, errors=0, wake still pending, NO 30s burn, NO
        enqueue. (Under M1 the tick burns 30 shim sleeps → RED.)"""
        _seed_incident_state(install)
        manager = _mock_manager()
        service = UpgradeJournalSweepService(install, manager=manager)
        with caplog.at_level(logging.WARNING):
            result = await service.sweep_wake_records()
        assert result.delivered == 0
        assert result.errors == 0
        assert result.pending_at_end == 1  # retryable — stays for next cycle
        assert manager.enqueue_message.await_count == 0
        assert fake_uj_time.sleep_calls == 0
        assert "own open in_flight txn" in caplog.text

    def test_foreign_holder_keeps_wait_semantics(
        self, install: Path, fake_uj_time: _FakeTime
    ) -> None:
        """Control: a FOREIGN holder (lock run_id ≠ wake run_id) keeps the
        original wait-then-skip semantics — the fix narrows ONLY the
        own-txn class. The wait loop IS entered (observed as shim
        sleeps), then the documented skip fires."""
        _seed_incident_state(install)
        # Re-key the held lock to a foreign run (the txn stays ours —
        # the point is the LOCK identity mismatch).
        lock_dir = uj.lock_dir(install)
        (lock_dir / "run_id").write_text("r-foreign-run\n", encoding="utf-8")
        result = uj.mark_wake_delivering(install, RUN_ID)
        assert result is None
        assert fake_uj_time.sleep_calls == 30  # the original semantics burn
        assert uj.lock_run_id(install) == "r-foreign-run"


# ── PART 2 — executor-exit closer hook ───────────────────────────────────────


class TestExecutorExitCloser:
    """The death leg: an OBSERVED executor exit on an unflipped restart
    txn closes the arm + releases the lock ⇒ the next cycle delivers."""

    @pytest.mark.asyncio
    async def test_exit_minus_one_closes_arm_then_next_cycle_delivers(
        self, install: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Today's shape (ChildProcessError leg → exit_code=-1): the
        closer fires on the reaper's journaling path — in_flight=None,
        pending_op cleared, restart_abandoned event WITH the exit
        attribution, lock released — and the NEXT sweep cycle delivers
        the wake (self-heal within one cycle; livelock arc closed)."""
        _seed_incident_state(install)
        manager = _mock_manager()
        service = UpgradeJournalSweepService(install, manager=manager)
        await service._journal_executor_exit(
            _reaper_job(install), exit_code=-1, signal_name=None, signal_num=None
        )
        data = journal_read(install)
        # The txn is closed.
        assert data.get("in_flight") is None
        assert data.get("pending_op") is None
        assert data.get("pending_restart") is None
        # The closure event: boot-lane vocabulary + exit attribution.
        events = [(e["event"], e["detail"]) for e in (data.get("history") or [])]
        abandoned = [d for (ev, d) in events if ev == "restart_abandoned"]
        assert len(abandoned) == 1
        assert "executor" in abandoned[0]
        assert "exit observed" in abandoned[0]
        assert "exit_code=-1" in abandoned[0]
        assert "flipped=false" in abandoned[0]
        # Existing behavior preserved: executor_exit still journaled.
        assert any(ev == "executor_exit" for (ev, _d) in events)
        # The lock is FREED.
        assert uj.lock_run_id(install) is None
        # THE HEAL ARC: the next cycle delivers the pending wake.
        result = await service.sweep_wake_records()
        assert result.delivered == 1
        assert result.errors == 0
        assert manager.enqueue_message.await_count == 1
        assert uj.list_pending_wakes(install) == []

    @pytest.mark.asyncio
    async def test_exit_78_closes_arm_the_same_way(self, install: Path) -> None:
        """The other cited shape (exit 78): both exit codes funnel into
        the same site, so the same closure + delivery arc holds."""
        _seed_incident_state(install)
        manager = _mock_manager()
        service = UpgradeJournalSweepService(install, manager=manager)
        await service._journal_executor_exit(
            _reaper_job(install), exit_code=78, signal_name=None, signal_num=None
        )
        data = journal_read(install)
        assert data.get("in_flight") is None
        assert data.get("pending_op") is None
        assert uj.lock_run_id(install) is None
        abandoned = [
            e["detail"]
            for e in (data.get("history") or [])
            if e["event"] == "restart_abandoned"
        ]
        assert len(abandoned) == 1
        assert "exit_code=78" in abandoned[0]
        result = await service.sweep_wake_records()
        assert result.delivered == 1

    @pytest.mark.asyncio
    async def test_flipped_txn_is_never_touched(
        self, install: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """halt-for-human stays: in_flight flipped=true (mutation
        happened) ⇒ the closer is a logged no-op — txn, pending_op and
        lock all untouched, no restart_abandoned event."""
        _seed_incident_state(install, flipped=True)
        service = UpgradeJournalSweepService(install, manager=_mock_manager())
        with caplog.at_level(logging.INFO):
            await service._journal_executor_exit(
                _reaper_job(install), exit_code=-1, signal_name=None, signal_num=None
            )
        data = journal_read(install)
        inf = data.get("in_flight")
        assert isinstance(inf, dict) and inf.get("flipped") is True
        assert data.get("pending_op") is not None
        assert uj.lock_run_id(install) == RUN_ID
        assert not any(
            e["event"] == "restart_abandoned" for e in (data.get("history") or [])
        )
        assert "flipped=true" in caplog.text

    @pytest.mark.asyncio
    async def test_foreign_run_exit_does_not_close_our_arm(self, install: Path) -> None:
        """Identity guard: the exit observed for a DIFFERENT run must
        never close our arm."""
        _seed_incident_state(install)
        service = UpgradeJournalSweepService(install, manager=_mock_manager())
        await service._journal_executor_exit(
            _reaper_job(install, run_id="r-other-run"),
            exit_code=-1,
            signal_name=None,
            signal_num=None,
        )
        data = journal_read(install)
        assert isinstance(data.get("in_flight"), dict)
        assert data.get("pending_op") is not None
        assert uj.lock_run_id(install) == RUN_ID
        assert not any(
            e["event"] == "restart_abandoned" for e in (data.get("history") or [])
        )

    @pytest.mark.asyncio
    async def test_exit_without_arm_envelope_is_harmless(self, install: Path) -> None:
        """No pending_op (plain promote flow / no arm) ⇒ the closer is a
        no-op — nothing to close, nothing written."""
        service = UpgradeJournalSweepService(install, manager=_mock_manager())
        await service._journal_executor_exit(
            _reaper_job(install), exit_code=0, signal_name=None, signal_num=None
        )
        data = journal_read(install)
        assert data.get("in_flight") is None
        assert not any(
            e["event"] == "restart_abandoned" for e in (data.get("history") or [])
        )
