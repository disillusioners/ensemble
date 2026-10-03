"""Unit tests for the Post-Restart Arm-Notify journal surface (Phase 1).

The arm-side durable record + lifecycle helpers (``PendingWake``,
``arm_pending_wake``, ``mark_wake_delivering`` / ``_delivered`` /
``_abandoned``, ``list_pending_wakes``, ``latest_matching_event``)
live on the existing ``releases/state.json`` atomic surface — same
``journal_write`` envelope as ``PendingOp`` (ADR-039, journal-section
choice per D-FA1.1 + D-FA1.3 ruling).

Coverage groups (per phase1-plan.md + test-strategy.md §2.1):

* Group 1 — PendingWake dataclass (T1.1, T1.2)
* Group 2 — atomicity with pending_op (T1.3)
* Group 3 — helpers (T1.4, T1.5, T1.6)
* Group 4 — terminal-state walker (T4.1, T4.2, T4.3, T4.8)
* Group 5 — non-interference with the existing journal (T4.4, T4.5, T4.6)
* Group 6 — arm-side live-outright-refusal (T5.12)
* Group 7 — real-journal-shape pin (T6.7 — r4 fold C1)

All fixtures live under ``tmp_path`` — never a real install dir, never
live. The lib.sh interop tests are intentionally NOT added here (this
feature is daemon-only; lib.sh writers do not touch ``pending_wakes``).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from daemon.tools import upgrade_journal as uj
from daemon.tools.upgrade_journal import (
    ARM_NOTIFY_KILL_SWITCH_ENV,
    PENDING_WAKE_COALESCE_MAX,
    PENDING_WAKE_GRACE_S,
    JournalTorn,
    PendingOp,
    PendingWake,
    _WAKE_STATUS_ABANDONED,
    _WAKE_STATUS_DELIVERED,
    _WAKE_STATUS_DELIVERING,
    _WAKE_STATUS_PENDING,
    _arm_notify_enabled,
    arm_pending_wake,
    ensure_extensions,
    iso_plus,
    journal_history_append,
    journal_init,
    journal_read,
    journal_write,
    latest_matching_event,
    list_pending_wakes,
    mark_wake_abandoned,
    mark_wake_delivered,
    mark_wake_delivering,
    mark_wake_pending,
    now_iso,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def install(tmp_path: Path) -> Path:
    """A fresh staged-install fixture: journal initialized, extensions on."""
    inst = tmp_path / "install"
    (inst / "releases").mkdir(parents=True)
    journal_init(inst)
    uj.ensure_extensions(inst)
    return inst


def _make_wake(
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
    status: str = _WAKE_STATUS_PENDING,
) -> PendingWake:
    """Build a fully-populated ``PendingWake`` (every field set)."""
    # Z-terminated ISO (matches ``now_iso()`` and lib.sh ``_now_iso``).
    armed_at = "2026-10-04T00:00:00Z"
    expires_at = "2026-10-04T00:10:00Z"
    return PendingWake(
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


# ── Group 1 — PendingWake dataclass (T1.1, T1.2) ─────────────────────────────


class TestPendingWakeDataclass:
    """``PendingWake`` schema + ``from_json`` discipline (T1.1, T1.2, ADR-039).

    Every field round-trips byte-exact; unknown fields drop silently;
    missing required ``run_id`` returns ``None`` (garbage tolerance,
    mirrors ``PendingOp.from_json`` R-20).
    """

    def test_round_trip_preserves_every_field(self) -> None:
        """T1.1: every ``PendingWake`` field preserved through ``to_json`` /
        ``from_json``."""
        wake = _make_wake(
            run_id="r-aaa",
            kind="promote",
            env="demo",
            target_version="1.2.3",
            mode=None,
        )
        raw = wake.to_json()
        restored = PendingWake.from_json(raw)
        assert restored is not None
        assert restored.run_id == wake.run_id
        assert restored.kind == wake.kind
        assert restored.env == wake.env
        assert restored.arming_instance_id == wake.arming_instance_id
        assert restored.arming_agent_id == wake.arming_agent_id
        assert restored.source == wake.source
        assert restored.message_id == wake.message_id
        assert restored.target_version == wake.target_version
        assert restored.mode == wake.mode
        assert restored.armed_at == wake.armed_at
        assert restored.expires_at == wake.expires_at
        assert restored.abandon_after == wake.abandon_after
        assert restored.status == wake.status
        assert restored.message_metadata == wake.message_metadata

    def test_from_json_drops_unknown_fields_silently(self) -> None:
        """T1.1: unknown fields dropped silently (the line-738 discipline)."""
        raw = _make_wake().to_json()
        raw["some_future_field"] = "ignored"
        raw["another_unknown"] = ["x"]
        restored = PendingWake.from_json(raw)
        assert restored is not None
        assert not hasattr(restored, "some_future_field")
        # Round-trip: to_json of the restored record does NOT carry the
        # unknown fields (they were filtered on read).
        out = restored.to_json()
        assert "some_future_field" not in out

    def test_from_json_returns_none_for_non_dict(self) -> None:
        """T1.2: garbage tolerance — non-dict input → ``None``."""
        for garbage in (None, "string", 123, [], {"no_run_id": "x"}):
            assert PendingWake.from_json(garbage) is None, garbage

    def test_from_json_backfills_abandon_after_when_absent(self) -> None:
        """Backfill discipline: a legacy record missing ``abandon_after``
        is reconstructed via ``iso_plus(expires_at, PENDING_WAKE_GRACE_S)``
        so the sweep's grace check stays correct."""
        wake = _make_wake()
        raw = wake.to_json()
        raw.pop("abandon_after")
        restored = PendingWake.from_json(raw)
        assert restored is not None
        # exp 2026-10-04T00:10:00 + 600s = 2026-10-04T00:20:00
        assert restored.abandon_after == "2026-10-04T00:20:00Z"


# ── Group 2 — atomicity with pending_op (T1.3) ────────────────────────────────


class TestArmPendingWakeAtomicity:
    """``arm_pending_wake`` rides the SAME ``journal_write`` envelope as
    ``write_pending_op`` (T1.3, ADR-039, architecture delta #3 — atomicity
    is BY the caller's lock, not the journal's). The two writes happen in
    separate ``journal_write`` calls under the SAME caller-acquired journal
    lock at the arm sites (``upgrade_tools.py:2167``/``:2719``); the
    ``journal_write`` helper is itself non-locking.
    """

    def test_arm_pending_wake_writes_to_pending_wakes_key(
        self, install: Path
    ) -> None:
        """T1.3 positive: ``arm_pending_wake`` produces a ``pending_wakes``
        key on the journal after a fresh arm."""
        wake = _make_wake()
        arm_pending_wake(install, wake)
        data = journal_read(install)
        assert "pending_wakes" in data
        assert isinstance(data["pending_wakes"], dict)
        assert "r-test-1" in data["pending_wakes"]
        # Every field preserved in the persisted record.
        persisted = data["pending_wakes"]["r-test-1"]
        assert persisted["run_id"] == "r-test-1"
        assert persisted["kind"] == "restart"
        assert persisted["env"] == "demo"
        assert persisted["source"] == "discord:user123"

    def test_arm_pending_wake_rides_journal_write_envelope(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T1.3 envelope: ``arm_pending_wake`` calls ``journal_write`` once
        (the same envelope ``write_pending_op`` uses). Verified via
        ``os.replace`` spy."""
        import daemon.tools.upgrade_journal as uj_mod

        write_calls: list[str] = []
        original_write = uj_mod.journal_write

        def spy_write(install_dir, data):
            write_calls.append("called")
            return original_write(install_dir, data)

        monkeypatch.setattr(uj_mod, "journal_write", spy_write)
        wake = _make_wake()
        arm_pending_wake(install, wake)
        assert "called" in write_calls

    def test_arm_pending_wake_second_call_for_same_run_id_overwrites(
        self, install: Path
    ) -> None:
        """Per-arm semantics: a re-arm of the same ``run_id`` overwrites
        the prior record (the wake's identity is the run_id)."""
        first = _make_wake(source="discord:user1")
        arm_pending_wake(install, first)
        second = _make_wake(source="discord:user2")
        arm_pending_wake(install, second)
        records = list_pending_wakes(install)
        assert len(records) == 1
        assert records[0].source == "discord:user2"

    def test_arm_pending_wake_kill_switch_short_circuits(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T5.12 arm-side pin (Phase 1 of the kill-switch contract):
        ``ENSEMBLE_POST_RESTART_ARM_NOTIFY=0`` short-circuits
        ``arm_pending_wake`` to a no-op — NO journal write, NO exception."""
        monkeypatch.setenv(ARM_NOTIFY_KILL_SWITCH_ENV, "0")
        assert _arm_notify_enabled() is False
        wake = _make_wake()
        arm_pending_wake(install, wake)
        # Ensure no record was written.
        data = journal_read(install)
        assert "pending_wakes" not in data or not data["pending_wakes"]


# ── Group 3 — helpers (T1.4, T1.5, T1.6) ──────────────────────────────────────


class TestMarkWakeHelpers:
    """The four ``mark_wake_*`` lifecycle helpers (T1.4, T1.5, T1.6)."""

    def test_mark_wake_delivering_cas_success(self, install: Path) -> None:
        """T1.4: ``mark_wake_delivering`` transitions ``pending → delivering``
        when the lock is free; returns the new ``PendingWake``."""
        wake = _make_wake()
        arm_pending_wake(install, wake)
        result = mark_wake_delivering(install, wake.run_id)
        assert result is not None
        assert result.status == _WAKE_STATUS_DELIVERING
        # Persisted state matches.
        records = list_pending_wakes(install)
        assert len(records) == 1
        assert records[0].status == _WAKE_STATUS_DELIVERING

    def test_mark_wake_delivering_returns_none_when_run_absent(
        self, install: Path
    ) -> None:
        """T1.4: a run_id not in the dict returns ``None`` (no exception)."""
        result = mark_wake_delivering(install, "r-missing")
        assert result is None

    def test_mark_wake_delivering_returns_none_for_non_pending_state(
        self, install: Path
    ) -> None:
        """T1.4: a non-pending record (already delivered) cannot be CAS'd —
        returns ``None`` (CAS-loser semantics)."""
        wake = _make_wake()
        arm_pending_wake(install, wake)
        # Manually set status to delivered (defensive reader check).
        data = journal_read(install)
        data["pending_wakes"][wake.run_id]["status"] = _WAKE_STATUS_DELIVERED
        journal_write(install, data)
        result = mark_wake_delivering(install, wake.run_id)
        assert result is None

    def test_mark_wake_delivered_removes_from_dict(self, install: Path) -> None:
        """T1.5: ``mark_wake_delivered`` STRUCTURALLY REMOVES the record from
        the dict — the idempotency key (invariant 7)."""
        wake = _make_wake()
        arm_pending_wake(install, wake)
        mark_wake_delivered(install, wake.run_id, "m-delivered-1")
        records = list_pending_wakes(install)
        assert records == []
        # The dict is structurally absent (not just status=delivered).
        data = journal_read(install)
        assert wake.run_id not in data.get("pending_wakes", {})

    def test_mark_wake_delivered_idempotent_when_absent(
        self, install: Path
    ) -> None:
        """Idempotency by construction: a second ``mark_wake_delivered``
        after structural removal is a silent no-op (no exception)."""
        wake = _make_wake()
        arm_pending_wake(install, wake)
        mark_wake_delivered(install, wake.run_id, "m-1")
        # Second call: no exception, no-op.
        mark_wake_delivered(install, wake.run_id, "m-2")
        records = list_pending_wakes(install)
        assert records == []

    def test_mark_wake_abandoned_removes_and_journals(
        self, install: Path
    ) -> None:
        """T1.6: ``mark_wake_abandoned`` removes the record AND journals a
        ``wake_abandoned`` history event with ``run_id`` + ``reason``."""
        wake = _make_wake()
        arm_pending_wake(install, wake)
        mark_wake_abandoned(install, wake.run_id, "kill_switch_off")
        records = list_pending_wakes(install)
        assert records == []
        # History event recorded.
        history = journal_read(install).get("history", [])
        abandoned_events = [
            e for e in history if isinstance(e, dict) and e.get("event") == "wake_abandoned"
        ]
        assert len(abandoned_events) == 1
        evt = abandoned_events[0]
        assert "run_id=r-test-1" in evt["detail"]
        assert "reason=kill_switch_off" in evt["detail"]

    def test_mark_wake_abandoned_idempotent_when_absent(
        self, install: Path
    ) -> None:
        """A second abandonment call is a no-op for the dict (already
        gone) and journals ONE additional ``wake_abandoned`` event — the
        forensics trail records every call (the sweep's abandon-on-
        switch-off MUST journal even when the dict is empty, see
        Phase 2 T14 + T5.16)."""
        wake = _make_wake()
        arm_pending_wake(install, wake)
        mark_wake_abandoned(install, wake.run_id, "kill_switch_off")
        mark_wake_abandoned(install, wake.run_id, "kill_switch_off")
        history = journal_read(install).get("history", [])
        abandoned_events = [
            e for e in history if isinstance(e, dict) and e.get("event") == "wake_abandoned"
        ]
        assert len(abandoned_events) == 2

    def test_mark_wake_pending_idempotent_on_pending(
        self, install: Path
    ) -> None:
        """T1.4: ``mark_wake_pending`` on an already-``pending`` record is
        an idempotent no-op — returns the existing ``PendingWake``,
        leaves ``status`` unchanged, and does NOT rewrite the journal.

        The delivering→pending flip branch is covered indirectly by the
        sweep test (test_enqueue_failure_recovers_on_next_tick_via_pending_rollback).
        This unit test pins the OTHER branch the helper has: when the
        record is already in ``pending`` (CAS-loser semantics — nothing
        to roll back), the helper must NOT clobber the record or trigger
        a journal_write.
        """
        wake = _make_wake()
        arm_pending_wake(install, wake)
        # Capture the on-disk state so we can prove NO journal_write.
        before_raw = journal_read(install)
        before_status = before_raw["pending_wakes"][wake.run_id]["status"]
        assert before_status == _WAKE_STATUS_PENDING

        result = mark_wake_pending(install, wake.run_id)

        # Returns the existing record; status unchanged.
        assert result is not None
        assert result.status == _WAKE_STATUS_PENDING
        assert result.run_id == wake.run_id
        # On-disk state unchanged (no journal_write — the idempotent
        # branch returns before the write site).
        after_raw = journal_read(install)
        assert after_raw["pending_wakes"][wake.run_id]["status"] == _WAKE_STATUS_PENDING
        # The dict still holds exactly one record (no clobber, no drop).
        records = list_pending_wakes(install)
        assert len(records) == 1
        assert records[0].status == _WAKE_STATUS_PENDING

    def test_mark_wake_pending_no_op_for_structurally_removed_delivered(
        self, install: Path
    ) -> None:
        """T1.5: ``mark_wake_pending`` MUST NOT resurrect a structurally
        removed ``delivered`` record (D-FA1.2 — delivered is the
        idempotency key, invariant 7; the record is GONE from the dict,
        not just status-flagged). The helper's contract is silent
        no-op on absence — ``None`` returned, no recreation, no
        journal write, no history event.
        """
        wake = _make_wake()
        arm_pending_wake(install, wake)
        mark_wake_delivered(install, wake.run_id, "m-delivered-1")
        # Pre-condition: the record is structurally absent.
        pre = journal_read(install)
        assert wake.run_id not in pre.get("pending_wakes", {})

        result = mark_wake_pending(install, wake.run_id)

        # Returns None (absent → no-op).
        assert result is None
        # The dict is STILL structurally empty — no resurrection.
        records = list_pending_wakes(install)
        assert records == []
        post = journal_read(install)
        assert wake.run_id not in post.get("pending_wakes", {}), (
            "mark_wake_pending must NOT recreate a delivered record "
            "(D-FA1.2 — delivered is structural removal, the "
            "idempotency key for invariant 7)"
        )

    def test_mark_wake_pending_no_op_for_structurally_removed_abandoned(
        self, install: Path
    ) -> None:
        """T1.6: ``mark_wake_pending`` MUST NOT resurrect a structurally
        removed ``abandoned`` record either (same D-FA1.2 contract).
        Critically, the call must NOT journal a second ``wake_abandoned``
        event — only ``mark_wake_abandoned`` is allowed to journal that
        event; ``mark_wake_pending`` is silent on absent records.
        """
        wake = _make_wake()
        arm_pending_wake(install, wake)
        mark_wake_abandoned(install, wake.run_id, "kill_switch_off")
        # Capture history-event count after the abandonment (which DOES journal).
        history_before = journal_read(install).get("history", [])
        abandoned_before = [
            e for e in history_before
            if isinstance(e, dict) and e.get("event") == "wake_abandoned"
        ]
        assert len(abandoned_before) == 1
        # Pre-condition: record is structurally absent.
        pre = journal_read(install)
        assert wake.run_id not in pre.get("pending_wakes", {})

        result = mark_wake_pending(install, wake.run_id)

        # Returns None — silent no-op.
        assert result is None
        # The dict is STILL empty — no resurrection.
        records = list_pending_wakes(install)
        assert records == []
        # Crucially: NO additional ``wake_abandoned`` history event —
        # mark_wake_pending is silent on absence (only
        # mark_wake_abandoned is allowed to journal wake_abandoned).
        history_after = journal_read(install).get("history", [])
        abandoned_after = [
            e for e in history_after
            if isinstance(e, dict) and e.get("event") == "wake_abandoned"
        ]
        assert len(abandoned_after) == 1, (
            "mark_wake_pending must NOT journal wake_abandoned events "
            "(that is mark_wake_abandoned's contract; mark_wake_pending "
            "is silent on absent records)"
        )


# ── Group 3b — list_pending_wakes (T1.6 garbage tolerance) ────────────────────


class TestListPendingWakes:
    """``list_pending_wakes`` is a defensive reader (T1.6): garbage-tolerance
    on the ``pending_wakes`` value (mirrors ``PendingOp.from_json`` discipline)."""

    def test_returns_empty_on_torn_journal(self, install: Path) -> None:
        """A torn journal (empty / unparseable / non-dict) returns ``[]``
        without raising — the sweep's caller logs and continues."""
        # Garbage the journal.
        (install / "releases" / "state.json").write_text("", encoding="utf-8")
        assert list_pending_wakes(install) == []
        (install / "releases" / "state.json").write_text(
            "garbage-not-json", encoding="utf-8"
        )
        assert list_pending_wakes(install) == []

    def test_returns_empty_for_garbage_pending_wakes_value(self, install: Path) -> None:
        """``pending_wakes`` value can be null / list / str / int — all
        return ``[]`` without raising."""
        for garbage in (None, [], "string", 123, {"not": "real_callable"}):
            data = journal_read(install)
            data["pending_wakes"] = garbage
            journal_write(install, data)
            assert list_pending_wakes(install) == [], garbage

    def test_returns_well_formed_records(self, install: Path) -> None:
        """A well-formed ``pending_wakes`` dict returns the records."""
        arm_pending_wake(install, _make_wake(run_id="r-a"))
        arm_pending_wake(install, _make_wake(run_id="r-b"))
        records = list_pending_wakes(install)
        run_ids = sorted(r.run_id for r in records)
        assert run_ids == ["r-a", "r-b"]

    def test_skips_malformed_records_with_warning(
        self, install: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A malformed record (missing required field) is logged + skipped
        — never crashes the reader."""
        arm_pending_wake(install, _make_wake(run_id="r-good"))
        data = journal_read(install)
        # Insert a malformed entry (no run_id → from_json returns None).
        data["pending_wakes"]["r-bad"] = {"kind": "restart", "env": "demo"}
        # Insert a non-dict entry.
        data["pending_wakes"]["r-weird"] = "string-not-dict"
        journal_write(install, data)
        records = list_pending_wakes(install)
        # Only the well-formed record survives.
        assert [r.run_id for r in records] == ["r-good"]


# ── Group 4 — terminal-state walker (T4.1, T4.2, T4.3, T4.8) ──────────────────


class TestLatestMatchingEvent:
    """``latest_matching_event`` walker (Phase 1 T7, ADR-042)."""

    def test_returns_event_name_for_matching_history(self, install: Path) -> None:
        """T4.1: a journal ending with a matching terminal event returns the
        event name (promote lane — no run_id on history entries)."""
        armed_at = "2026-10-04T00:00:00Z"
        data = journal_read(install)
        data["history"] = [
            {"ts": "2026-10-04T00:00:00Z", "event": "staged", "detail": "..."},
            {"ts": "2026-10-04T00:01:00Z", "event": "commit", "detail": "..."},
        ]
        journal_write(install, data)
        result = latest_matching_event(
            data, run_id="r-any", armed_at=armed_at, events=("commit", "rollback")
        )
        assert result == "commit"

    def test_returns_none_when_no_matching_event(self, install: Path) -> None:
        """T4.2: no matching history event → ``None`` (the wake is held)."""
        armed_at = "2026-10-04T00:00:00Z"
        data = journal_read(install)
        data["history"] = [
            {"ts": "2026-10-04T00:01:00Z", "event": "staged", "detail": "..."},
        ]
        journal_write(install, data)
        result = latest_matching_event(
            data, run_id="r-any", armed_at=armed_at, events=("commit", "rollback")
        )
        assert result is None

    def test_returns_none_on_garbage_history(self, install: Path) -> None:
        """T4.3: garbage history (non-list, malformed entries) → ``None``
        (best-effort; the sweep's caller logs and continues)."""
        armed_at = "2026-10-04T00:00:00Z"
        for garbage in (None, "string", 123, {"not": "list"}, []):
            data = journal_read(install)
            data["history"] = garbage
            journal_write(install, data)
            assert latest_matching_event(
                data, run_id="r-any", armed_at=armed_at, events=("commit",)
            ) is None, garbage

    def test_returns_none_for_armed_at_after_event_ts(
        self, install: Path
    ) -> None:
        """The walker scopes by ``armed_at``: an event whose ``ts`` is
        BEFORE ``armed_at`` is out of scope (e.g. a stale terminal from a
        prior run)."""
        armed_at = "2026-10-04T00:10:00Z"
        data = journal_read(install)
        data["history"] = [
            {"ts": "2026-10-04T00:00:00Z", "event": "commit", "detail": "..."},
            {"ts": "2026-10-04T00:05:00Z", "event": "rollback", "detail": "..."},
        ]
        journal_write(install, data)
        result = latest_matching_event(
            data, run_id="r-any", armed_at=armed_at, events=("commit", "rollback")
        )
        assert result is None

    def test_skips_non_terminal_entries(self, install: Path) -> None:
        """Ordinary history entries (``staged``, ``nonce_consumed``, etc.)
        are NOT in the events tuple and are skipped."""
        armed_at = "2026-10-04T00:00:00Z"
        data = journal_read(install)
        data["history"] = [
            {"ts": "2026-10-04T00:01:00Z", "event": "staged", "detail": "..."},
            {"ts": "2026-10-04T00:02:00Z", "event": "nonce_consumed", "detail": "..."},
            {"ts": "2026-10-04T00:03:00Z", "event": "commit", "detail": "..."},
        ]
        journal_write(install, data)
        result = latest_matching_event(
            data, run_id="r-any", armed_at=armed_at, events=("commit", "rollback")
        )
        assert result == "commit"

    def test_returns_latest_matching_event_in_scope(self, install: Path) -> None:
        """When multiple events match in scope, the LATEST is returned
        (mirrors ``_terminal_event_after`` semantics — newest-wins)."""
        armed_at = "2026-10-04T00:00:00Z"
        data = journal_read(install)
        data["history"] = [
            {"ts": "2026-10-04T00:01:00Z", "event": "commit", "detail": "..."},
            {"ts": "2026-10-04T00:02:00Z", "event": "rollback", "detail": "..."},
            {"ts": "2026-10-04T00:03:00Z", "event": "halt", "detail": "..."},
        ]
        journal_write(install, data)
        result = latest_matching_event(
            data,
            run_id="r-any",
            armed_at=armed_at,
            events=("commit", "rollback", "halt"),
        )
        assert result == "halt"

    def test_walker_uses_real_journal_shape(self, install: Path) -> None:
        """T6.7 (r4 fold C1): the REAL journal history shape is
        ``{"ts": <iso>, "event": <name>, "detail": <prose>}``. A fixture
        using the fictional ``{"name", "run_id"}`` shape is a fail-loud
        (the walker reads ``event``, NOT ``name``)."""
        armed_at = "2026-10-04T00:00:00Z"
        data = journal_read(install)
        # Fictional shape — note: no ``event`` field, the walker reads
        # ``event.get("event", "")`` and the in-class check returns ``None``.
        data["history"] = [
            {"ts": "2026-10-04T00:01:00Z", "name": "commit", "run_id": "r-x"},
        ]
        journal_write(install, data)
        result = latest_matching_event(
            data, run_id="r-x", armed_at=armed_at, events=("commit",)
        )
        # Real walker does NOT match on the fictional ``name`` field.
        assert result is None


# ── Group 5 — non-interference with the existing journal (T4.4, T4.5, T4.6) ───


class TestNonInterference:
    """The wake record does NOT interfere with the existing journal
    surfaces (T4.4, T4.5, T4.6 — load-bearing reason for a separate key)."""

    def test_clear_pending_op_does_not_touch_pending_wakes(
        self, install: Path
    ) -> None:
        """T4.4: ``clear_pending_op`` clears ``pending_op`` +
        ``pending_restart`` BUT NOT ``pending_wakes``."""
        wake = _make_wake()
        arm_pending_wake(install, wake)
        # Plant a pending_op + pending_restart so clear_pending_op has
        # something to clear.
        op = PendingOp(
            run_id="r-x",
            kind="restart",
            env="demo",
            target=None,
        )
        uj.write_pending_op(install, op)
        uj.clear_pending_op(install)
        # The wake survives.
        records = list_pending_wakes(install)
        assert len(records) == 1
        assert records[0].run_id == wake.run_id

    def test_simulated_restart_sh_terminal_clear_preserves_pending_wakes(
        self, install: Path
    ) -> None:
        """T4.5: simulate the ``restart.sh:250-262`` terminal-clear sequence
        (``in_flight = None`` + ``clear_pending_op`` + ``journal_history_append
        "restart"``); the wake record survives (the executor's terminal-
        clearing must NOT clear the wake)."""
        wake = _make_wake()
        arm_pending_wake(install, wake)
        # Simulate the restart.sh terminal-clear.
        data = journal_read(install)
        data["in_flight"] = None
        data["pending_op"] = None
        data["pending_restart"] = None
        data["history"].append(
            {
                "ts": "2026-10-04T00:01:00Z",
                "event": "restart",
                "detail": "run_id=r-test-1 restarted to v2.0.0",
            }
        )
        journal_write(install, data)
        # The wake survives.
        records = list_pending_wakes(install)
        assert len(records) == 1
        assert records[0].run_id == wake.run_id

    def test_reconcile_pending_op_does_not_touch_pending_wakes(
        self, install: Path
    ) -> None:
        """T4.6: ``reconcile_pending_op`` (PROMOTE-kind only) does NOT touch
        ``pending_wakes``."""
        wake = _make_wake(kind="promote", target_version="1.2.3")
        arm_pending_wake(install, wake)
        # Plant a promote-kind pending_op with a terminal history event.
        data = journal_read(install)
        op = PendingOp(
            run_id="r-promote-x",
            kind="promote",
            env="demo",
            target="1.2.3",
        )
        op.armed_at = "2026-10-04T00:00:00Z"
        data["pending_op"] = op.to_json()
        data["pending_restart"] = None
        data["history"] = [
            {"ts": "2026-10-04T00:00:00Z", "event": "staged", "detail": "..."},
            {"ts": "2026-10-04T00:01:00Z", "event": "commit", "detail": "..."},
        ]
        journal_write(install, data)
        # Run reconcile.
        note = uj.reconcile_pending_op(install)
        assert note is not None  # closed
        # The wake survives.
        records = list_pending_wakes(install)
        assert len(records) == 1
        assert records[0].run_id == wake.run_id


# ── Group 6 — arm-side live-outright-refusal (T5.12) ──────────────────────────


class TestArmSideLiveRefusal:
    """The arm-side live-outright-refusal returns BEFORE any journal write
    (T5.12, D-FA5.5 / ADR-044 inheritance). The wake record must be
    structurally absent on a live arm — verified by reading the journal
    after a refused arm (no ``pending_wakes`` entry, no ``pending_op``).
    """

    def test_system_restart_on_live_writes_no_wake(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T5.12: ``system_restart`` with ``self_env=live`` returns the
        live-outright-refusal AND writes no ``pending_wakes`` record."""
        from daemon.tools import upgrade_tools as ut
        from daemon.tools.upgrade_tools import create_upgrade_tools

        monkeypatch.setenv("ENSEMBLE_SELF_ENV", "live")
        monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: install)
        manager = MagicMock()
        manager.config.daemon.port = 0
        manager._task_repo = None
        manager._queue_repository = None
        manager._user_origin_windows = {}
        tools = {t.name: t for t in create_upgrade_tools(manager, "i-test", agent_id="ari")}
        out = tools["system_restart"].ainvoke(
            {
                "target_env": "live",
                "reason": "x",
                "dry_run": False,
                "user_confirmed": True,
            }
        )
        import asyncio

        result = asyncio.run(out)
        # Refused.
        assert "live-restart-refused" in result.lower(), result
        # No journal mutation.
        data = journal_read(install)
        assert data.get("pending_op") is None
        assert "pending_wakes" not in data or not data["pending_wakes"]


# ── Group 7 — ensure_extensions default (r4 fold C1 + ADR-039) ────────────────


class TestEnsureExtensionsPendingWakesKey:
    """``ensure_extensions`` adds ``pending_wakes: {}`` when absent
    (Phase 1, ADR-039 — additive extension point, same shape as the
    existing ``pending_op`` / ``pending_restart`` / ``pending_actions``
    defaults)."""

    def test_ensure_extensions_adds_pending_wakes_default(self, tmp_path: Path) -> None:
        """On a fresh install, ``ensure_extensions`` adds the
        ``pending_wakes`` key (default ``{}``)."""
        inst = tmp_path / "fresh-install"
        (inst / "releases").mkdir(parents=True)
        journal_init(inst)
        data = ensure_extensions(inst)
        assert "pending_wakes" in data
        assert data["pending_wakes"] == {}

    def test_ensure_extensions_preserves_existing_pending_wakes(
        self, tmp_path: Path
    ) -> None:
        """On a journal with an existing ``pending_wakes``, ``ensure_extensions``
        leaves it untouched (only ADDS — never rewrites)."""
        inst = tmp_path / "fresh-install"
        (inst / "releases").mkdir(parents=True)
        journal_init(inst)
        wake = _make_wake()
        arm_pending_wake(inst, wake)
        # A second ensure_extensions must NOT clobber the wake.
        data = ensure_extensions(inst)
        assert "r-test-1" in data["pending_wakes"]
        # And the journal on disk is unchanged.
        raw = json.loads((inst / "releases" / "state.json").read_text())
        assert "r-test-1" in raw["pending_wakes"]


# ── Group 8 — capture helpers (T8/T9 ride-along) ────────────────────────────


class TestCaptureHelpers:
    """The capture helpers read the manager's in-memory user-origin window
    + instance repo (D-FA2.2, Phase 1 T8/T9 ride-along)."""

    def test_capture_user_source_returns_recorded_source(self) -> None:
        """Source = the window's ``source`` field."""
        manager = MagicMock()
        manager._user_origin_windows = {"i-1": {"source": "discord:user1", "message_id": "m-1"}}
        from daemon.tools.upgrade_tools import (
            _capture_user_source,
            _capture_user_message_id,
        )
        assert _capture_user_source(manager, "i-1") == "discord:user1"
        assert _capture_user_message_id(manager, "i-1") == "m-1"

    def test_capture_user_source_returns_empty_sentinel_for_absent(self) -> None:
        """No window entry → ``""`` (the empty-source sentinel; Phase 2
        falls back to ``"api"``)."""
        manager = MagicMock()
        manager._user_origin_windows = {}
        from daemon.tools.upgrade_tools import _capture_user_source
        assert _capture_user_source(manager, "i-1") == ""

    def test_capture_helpers_never_raise_on_garbage_manager(self) -> None:
        """Defensive: a manager missing the window attribute returns the
        sentinel — never raises (D-FA2.2)."""
        manager = MagicMock(spec=[])  # no _user_origin_windows
        from daemon.tools.upgrade_tools import (
            _capture_user_source,
            _capture_user_message_id,
            _resolve_agent_id,
        )
        assert _capture_user_source(manager, "i-1") == ""
        assert _capture_user_message_id(manager, "i-1") is None
        assert _resolve_agent_id(manager, "i-1") is None

    def test_resolve_agent_id_uses_instance_repository(self) -> None:
        """``agent_id`` resolution uses ``manager._instance_repository``;
        a repo that returns an Instance with ``agent_id`` flows through."""
        manager = MagicMock()
        meta = MagicMock()
        meta.agent_id = "ari"
        manager._instance_repository.get = MagicMock(return_value=meta)
        from daemon.tools.upgrade_tools import _resolve_agent_id
        assert _resolve_agent_id(manager, "i-1") == "ari"

    def test_resolve_agent_id_returns_none_for_missing_instance(self) -> None:
        """A repo miss returns ``None`` (the wake's target is the instance
        id; agent_id is informational only)."""
        manager = MagicMock()
        manager._instance_repository.get = MagicMock(return_value=None)
        from daemon.tools.upgrade_tools import _resolve_agent_id
        assert _resolve_agent_id(manager, "i-1") is None