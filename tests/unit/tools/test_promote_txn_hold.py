"""The A3 promote-transaction hold.

soak-gate-mcp-warmup · A3 · the normative source is
``.agents/shared/planning/soak-gate-mcp-warmup/requirements.md`` §4.7
(P7 — the promote-txn LIVE predicate, the release rule, the two-read
hysteresis and the driver).

The pins cover:

* (a) no resume is dispatched while the txn is open;
* (b) the deferred candidate is released through the ORIGINAL seam (the
  checkpoint-resume path) after the txn closes;
* (c) the hold is observable (counters + the reason token);
* (d) with no txn present the behaviour is unchanged;
* (e) the BOOT-WINDOW seam-1 timing case — a txn whose heartbeat is
  ALREADY stale still HOLDS at seam 1 (the 002 hazard);
* (f) the torn-``state.json`` release leg — ONE torn read must NOT
  release; the release needs TWO consecutive false reads ≥ 1 driver tick
  apart;
* (g) the READ-ONLY pin — the hold path performs ZERO journal writes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from daemon.services import promote_txn_hold as hold
from daemon.services.auto_continue_boot_pass import (
    continue_running_instances_after_restart,
)


# ── the scratch install root ───────────────────────────────────────────


@pytest.fixture()
def scratch_install(tmp_path: Path):
    """A scratch install root with a ``releases/state.json``."""
    releases = tmp_path / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    state = releases / "state.json"
    state.write_text(json.dumps({"current": "v0.0.0", "in_flight": None}))
    hold.set_install_dir_override(tmp_path)
    hold.reset_state()
    try:
        yield tmp_path, state
    finally:
        hold.set_install_dir_override(None)
        hold.reset_state()


def _write_state(state: Path, in_flight) -> None:
    state.write_text(json.dumps({"current": "v0.0.0", "in_flight": in_flight}))


def _promote_txn(*, heartbeat_fresh: bool, started_at: datetime | None = None):
    return {
        "kind": "promote",
        "target": "v0.0.1",
        "started_at": (
            started_at or datetime.now(tz=timezone.utc)
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "flipped": True,
        "owner_pid": 1234,
        "last_heartbeat": int(time.time()) if heartbeat_fresh else 0,
    }


# ── the predicate (§4.7) ───────────────────────────────────────────────


class TestPromoteTxnPredicate:
    def test_no_txn_is_not_live(self, scratch_install):
        _root, state = scratch_install
        _write_state(state, None)
        assert hold.promote_txn_live() is False

    def test_a_non_promote_txn_is_not_live(self, scratch_install):
        _root, state = scratch_install
        _write_state(state, {"kind": "restart", "last_heartbeat": int(time.time())})
        assert hold.promote_txn_live() is False

    def test_a_promote_txn_with_a_fresh_heartbeat_is_live(self, scratch_install):
        _root, state = scratch_install
        _write_state(state, _promote_txn(heartbeat_fresh=True))
        assert hold.promote_txn_live() is True

    def test_the_boot_window_leg_holds_with_a_stale_heartbeat(self, scratch_install):
        """(e) The 002 hazard: at seam 1 the heartbeat window can already
        be expired (the daemon is ~75 s old at its first green readyz).
        The BOOT-WINDOW leg must still HOLD."""
        _root, state = scratch_install
        _write_state(state, _promote_txn(heartbeat_fresh=False))
        # Post-boot (old daemon) ⇒ the stale heartbeat means NOT live.
        hold.set_boot_age_override(600.0)
        assert hold.promote_txn_live() is False
        # Boot window (young daemon) ⇒ the boot-window leg holds.
        hold.set_boot_age_override(10.0)
        assert hold.promote_txn_live() is True
        hold.set_boot_age_override(None)

    def test_the_boot_window_leg_expires_after_the_total_deadline(self, scratch_install):
        _root, state = scratch_install
        stale_start = datetime.now(tz=timezone.utc).replace(
            microsecond=0
        ) - __import__("datetime").timedelta(
            seconds=hold.PROMOTE_TOTAL_DEADLINE_S + 60
        )
        _write_state(state, _promote_txn(heartbeat_fresh=False, started_at=stale_start))
        hold.set_boot_age_override(10.0)
        assert hold.promote_txn_live() is False
        hold.set_boot_age_override(None)

    def test_a_torn_journal_is_not_live_and_is_not_release_authorized(
        self, scratch_install
    ):
        _root, state = scratch_install
        state.write_text("{ this is not json")
        live, release = hold.evaluate_promote_txn()
        assert live is False
        assert release is False


# ── the deferral registry + the release driver ────────────────────────


class TestHoldDeferral:
    @pytest.mark.asyncio
    async def test_a_deferred_candidate_is_released_after_the_txn_closes(
        self, scratch_install, monkeypatch
    ):
        _root, state = scratch_install
        monkeypatch.setattr(hold, "RELEASE_DRIVER_TICK_S", 0.05)
        released: list = []

        async def _handler(candidates):
            released.extend(candidates)

        hold.register_release_handler(hold.SEAM_BOOT_CONTINUE, _handler)

        _write_state(state, _promote_txn(heartbeat_fresh=True))
        assert hold.promote_txn_live() is True
        cand = hold.defer_resume(
            hold.SEAM_BOOT_CONTINUE, {"instance_id": "i-1", "work_id": "w-1"}
        )
        assert hold.pending_count() == 1
        snapshot = hold.hold_snapshot()
        assert snapshot["deferred"] == 1
        assert snapshot["reason"] == "pipeline-busy"
        assert snapshot["per_seam"][hold.SEAM_BOOT_CONTINUE] == 1

        # The txn closes.
        _write_state(state, None)
        assert hold.promote_txn_live() is False

        # The driver releases it (two consecutive false reads, ≥ 1 tick
        # apart).
        for _ in range(60):
            if released:
                break
            await asyncio.sleep(0.05)
        assert released, "the deferred candidate was NOT released"
        assert released[0].seam == hold.SEAM_BOOT_CONTINUE
        assert released[0].payload["instance_id"] == "i-1"
        assert hold.pending_count() == 0
        await hold.shutdown()

    @pytest.mark.asyncio
    async def test_one_torn_read_does_not_release(self, scratch_install, monkeypatch):
        """(f) The release hysteresis: a single torn/unreadable
        ``state.json`` read returns False and MUST NOT release."""
        _root, state = scratch_install
        monkeypatch.setattr(hold, "RELEASE_DRIVER_TICK_S", 0.05)
        released: list = []

        async def _handler(candidates):
            released.extend(candidates)

        hold.register_release_handler(hold.SEAM_BOOT_CONTINUE, _handler)
        _write_state(state, _promote_txn(heartbeat_fresh=True))
        hold.defer_resume(hold.SEAM_BOOT_CONTINUE, {"instance_id": "i-1"})

        # The journal goes TORN (one unreadable read).
        state.write_text("{ torn")
        await asyncio.sleep(0.2)
        assert not released, "a single torn read released the deferral"
        assert hold.pending_count() == 1

        # The journal recovers and the txn is gone ⇒ the release happens
        # (two consecutive false reads).
        _write_state(state, None)
        for _ in range(60):
            if released:
                break
            await asyncio.sleep(0.05)
        assert released, "the deferral was never released after recovery"
        await hold.shutdown()

    @pytest.mark.asyncio
    async def test_the_hold_path_writes_nothing(self, scratch_install, monkeypatch):
        """(g) The READ-ONLY pin — the scratch journal's bytes are
        unchanged across the hold ticks."""
        _root, state = scratch_install
        monkeypatch.setattr(hold, "RELEASE_DRIVER_TICK_S", 0.05)
        before = hashlib.sha256(state.read_bytes()).hexdigest()
        _write_state(state, _promote_txn(heartbeat_fresh=True))
        after_write = hashlib.sha256(state.read_bytes()).hexdigest()
        assert before != after_write  # the fixture write DID change it
        for _ in range(3):
            hold.promote_txn_live()
            await asyncio.sleep(0.05)
        hold.defer_resume(hold.SEAM_BOOT_CONTINUE, {"instance_id": "i-1"})
        await asyncio.sleep(0.2)
        assert hashlib.sha256(state.read_bytes()).hexdigest() == after_write
        await hold.shutdown()


# ── seam 1 — the boot auto-continue pass ──────────────────────────────


class TestReleaseFailureRetention:
    """F4 — a failed release is RETAINED, never dropped."""

    async def test_a_raising_handler_does_not_lose_the_candidate(self, scratch_install):
        """A raising seam handler must not DROP the candidate.

        The lane's own contract is "DEFERS, never drops": a release that
        FAILS must be retried on the next driver pass, not silently
        abandoned.
        """
        async def _boom(candidates):
            raise RuntimeError("the seam handler failed")

        hold.register_release_handler("seam-under-test", _boom)
        candidate = hold.defer_resume("seam-under-test", {"k": 1})
        assert hold.pending_count() == 1

        await hold._release_candidates([candidate])

        assert hold.pending_count() == 1, (
            "a raising release handler must NOT lose the candidate — the "
            "hold DEFERS, never drops"
        )
        assert hold._DEFERRED["seam-under-test"][0] is candidate, (
            "the RETAINED candidate must be the SAME object"
        )

    async def test_the_removal_is_by_identity_not_value_equality(self, scratch_install):
        """Two field-EQUAL candidates must not collide on removal.

        ``DeferredCandidate`` is a dataclass, so two candidates with the
        same fields compare EQUAL — a value-based ``not in`` removal
        would drop the wrong one.
        """
        async def _handler(candidates):
            return None

        hold.register_release_handler("seam-under-test", _handler)
        first = hold.defer_resume("seam-under-test", {"k": 1})
        second = hold.defer_resume("seam-under-test", {"k": 1})
        # Force the value-equality collision (the timestamps differ by
        # default).
        second.deferred_at = first.deferred_at
        assert first == second, (
            "the two candidates must compare EQUAL — that is exactly the "
            "collision a value-based removal had"
        )
        assert first is not second
        assert hold.pending_count() == 2

        await hold._release_candidates([first])

        assert hold.pending_count() == 1, (
            "releasing ONE of two field-equal candidates must not remove "
            "the other (the removal is by identity)"
        )
        assert hold._DEFERRED["seam-under-test"][0] is second


class _FakeTask:
    def __init__(self, task_id: str, instance_id: str, work_id: str):
        self.id = task_id
        self.instance_id = instance_id
        self.work_id = work_id


class _FakeTaskRepo:
    def __init__(self, candidates):
        self._candidates = candidates
        self.stamped: list = []

    def find_auto_continue_candidates(self, boot_epoch):
        return list(self._candidates)

    def mark_task_auto_continued(self, task_id, boot_epoch):
        self.stamped.append(task_id)
        return True

    def has_instance_busy(self, instance_id):
        return False


class _FakeManager:
    def __init__(self, candidates):
        self._task_repo = _FakeTaskRepo(candidates)
        self.resumes: list = []
        self.mission_record_service = None

    async def _has_checkpoint(self, instance_id):
        return True

    async def _schedule_explicit_handle_resume(self, **kwargs):
        self.resumes.append(kwargs)
        return {"status": "resuming"}


class TestBootPassSeam:
    @pytest.mark.asyncio
    async def test_no_resume_is_dispatched_while_the_txn_is_open(
        self, scratch_install
    ):
        _root, state = scratch_install
        _write_state(state, _promote_txn(heartbeat_fresh=True))
        manager = _FakeManager(
            [_FakeTask("t-1", "instance-0001", "work-0001")]
        )
        result = await continue_running_instances_after_restart(
            manager, datetime.now(tz=timezone.utc)
        )
        assert manager.resumes == [], "a resume was dispatched during the txn"
        assert result.deferred_pipeline_busy == 1
        assert result.deferred_work_ids == ["work-0001"]
        assert result.scheduled == 0
        assert hold.pending_count() == 1

    @pytest.mark.asyncio
    async def test_the_deferred_candidate_resumes_through_the_checkpoint_path(
        self, scratch_install, monkeypatch
    ):
        """(b) The release goes through the ORIGINAL seam — the
        checkpoint-resume path (``_schedule_explicit_handle_resume``),
        never a cold dispatch."""
        _root, state = scratch_install
        monkeypatch.setattr(hold, "RELEASE_DRIVER_TICK_S", 0.05)
        _write_state(state, _promote_txn(heartbeat_fresh=True))
        manager = _FakeManager(
            [_FakeTask("t-1", "instance-0001", "work-0001")]
        )
        await continue_running_instances_after_restart(
            manager, datetime.now(tz=timezone.utc)
        )
        assert manager.resumes == []
        # The txn closes → the hold releases through the boot pass's own
        # resume primitive.
        _write_state(state, None)
        for _ in range(60):
            if manager.resumes:
                break
            await asyncio.sleep(0.05)
        assert manager.resumes, "the deferred candidate never resumed"
        assert manager.resumes[0]["handle_work_id"] == "work-0001"
        assert manager.resumes[0]["route_outcome"] == "boot_continue"
        assert manager._task_repo.stamped == ["t-1"]
        await hold.shutdown()

    @pytest.mark.asyncio
    async def test_with_no_txn_the_behaviour_is_unchanged(self, scratch_install):
        """(d) No txn ⇒ the pass resumes immediately (byte-identical to
        today's behaviour)."""
        _root, state = scratch_install
        _write_state(state, None)
        manager = _FakeManager(
            [_FakeTask("t-1", "instance-0001", "work-0001")]
        )
        result = await continue_running_instances_after_restart(
            manager, datetime.now(tz=timezone.utc)
        )
        assert len(manager.resumes) == 1
        assert result.scheduled == 1
        assert result.deferred_pipeline_busy == 0
        assert hold.pending_count() == 0
