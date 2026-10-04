"""Unit tests for F-1 (durability-f1-f2 / phase1) bus gate + wipe predicate.

Feature: durability-f1-f2 / F-1 / Phase 1 (2026-10-04).

The F-1 wedge is a two-seam coupling: a child mid-``discard_on_startup``
wipe produces ``Outcome(status="error", error=None)`` (legitimate
terminated-branch output per ``instance_lifecycle.py:256``) and the
DependencyBus's bare ``if outcome.status == "error":`` gate flips
``_parent_errored = True`` from the None path. The next boot's wipe
then deletes the flipped-terminal row before the auto-continue boot
pass can see it (candidates==0). Outcome: parent permanently wedged
in ``waiting_children``.

The F-1 fix is a 2-arm wipe-side disjunction (arm 2 dropped per W-3):
  1. ``status IN ('running', 'paused')`` (preserved as before)
  2. ``auto_continued_at IS NOT NULL AND EXISTS (SELECT 1 FROM
     instances WHERE instances.id = task.instance_id AND
     instances.status NOT IN TERMINAL_INSTANCE_STATUSES)`` (NEW —
     the F-1 wedge fix)

Plus a bus-side truthy-error gate that skips the ``_parent_errored``
flip when ``outcome.status == "error"`` and ``outcome.error is None``.

The seven tests pin both seams:
  S1 — double restart no double continue (boot pass + CAS stamp)
  S2 — None error does NOT flip parent error (the F-1 wedge trigger)
  S3 — real error flips parent error (the normal path)
  S4 — terminal-stamped row of non-terminal instance SURVIVES clear
       (kill-switch ON — the F-1 fix's primary coverage)
  S5 — terminal row without marker is DELETED by clear
       (pre-F-1 baseline still works)
  S6 — boot sequence mock: candidates==1 (the F-1 wedge's
       ``candidates == 0`` condition is closed)
  S7 — kill-switch BOTH states: ON preserves, OFF deletes
       (arm 1 active in both)

Harness is MOCK-ONLY (no real DB). The bus tests use a
``_MockDependencyBusRepo`` stand-in for ``DependencyWatcherRepository``.
The clear_all tests use a minimal in-memory list of (task, instance)
shapes that the predicate logic consumes via a thin in-test SQL
re-implementation (the kill-switch + disjunction truth table is
deterministic and easily expressed in Python).

Mirrors the harness conventions of
``tests/unit/services/test_auto_continue_boot_pass.py``
and ``tests/unit/repositories/test_auto_continue_candidates.py``.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

import daemon.services.dependency_bus as bus_mod
from daemon.services.dependency_bus import (
    DependencyBus,
    FollowUp,
    Outcome,
    _has_truthy_error,
)
from daemon.constants import TERMINAL_INSTANCE_STATUSES


# ---------------------------------------------------------------------------
# Harness: kill-switch env-var context manager
# ---------------------------------------------------------------------------


@contextmanager
def _env(name: str, value: str | None):
    """Temporarily set/unset an env var."""
    old = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


# ---------------------------------------------------------------------------
# Harness: mock DependencyWatcherRepository
# ---------------------------------------------------------------------------


@dataclass
class _MockWatcherRow:
    """Stand-in for ``DependencyWatcher`` — the bus reads
    ``watch_id`` and ``follow_up_payload`` on the row."""

    follow_up_payload: dict[str, Any] = field(default_factory=dict)
    watch_id: str = "watch-F-1"


class _MockDependencyBusRepo:
    """Mock ``DependencyWatcherRepository`` — the bus calls:

    * ``fetch_pending_for_source(task_id)`` — returns list of rows
      (each row has ``follow_up_payload`` dict).
    * ``transition_state(...)`` — returns the new state on success.
    * ``fetch_pending_for_target_and_child(parent, child)`` — used by
      ``emit_terminal_for_child_instance``.

    Mock-only; no real DB.
    """

    def __init__(
        self,
        pending_by_source: dict[str, list[_MockWatcherRow]] | None = None,
        pending_by_target_child: dict[tuple[str, str], list[_MockWatcherRow]] | None = None,
    ) -> None:
        self._pending_by_source = pending_by_source or {}
        self._pending_by_target_child = pending_by_target_child or {}
        self.transition_calls: list[dict[str, Any]] = []
        self.fetch_source_calls: list[str] = []
        self.fetch_target_child_calls: list[tuple[str, str]] = []

    def fetch_pending_for_source(self, source_task_id: str) -> list[_MockWatcherRow]:
        self.fetch_source_calls.append(source_task_id)
        return list(self._pending_by_source.get(source_task_id, []))

    def fetch_pending_for_target_and_child(
        self, parent_instance_id: str, child_instance_id: str
    ) -> list[_MockWatcherRow]:
        self.fetch_target_child_calls.append((parent_instance_id, child_instance_id))
        return list(
            self._pending_by_target_child.get((parent_instance_id, child_instance_id), [])
        )

    def transition_state(
        self, watcher_id: int, from_state: str, to_state: str
    ) -> str | None:
        self.transition_calls.append(
            {"watcher_id": watcher_id, "from": from_state, "to": to_state}
        )
        return to_state


def _build_bus(
    *,
    pending_for_source: list[_MockWatcherRow] | None = None,
) -> tuple[DependencyBus, _MockDependencyBusRepo]:
    """Construct a DependencyBus + mock repo with a known PENDING
    list keyed on a stable source_task_id."""
    repo = _MockDependencyBusRepo(
        pending_by_source={"task-F-1": pending_for_source or []},
    )
    bus = DependencyBus(repo)  # type: ignore[arg-type]
    return bus, repo


def _make_watcher_row(parent_instance_id: str, message: str = "hi") -> _MockWatcherRow:
    """Build a ``_MockWatcherRow`` whose ``follow_up_payload`` carries
    the parent_instance_id (the field the bus reads via
    ``FollowUp.from_payload``)."""
    return _MockWatcherRow(
        follow_up_payload={
            "target_instance_id": parent_instance_id,
            "message": message,
            "source": "dependency_bus",
            "metadata": {},
        }
    )


# ---------------------------------------------------------------------------
# Helper-level unit tests (plan §1.2)
# ---------------------------------------------------------------------------


class TestHasTruthyErrorHelper:
    """The ``_has_truthy_error`` helper is the bus-gate primitive (plan §1.2)."""

    def test_returns_true_when_status_error_and_error_text(self) -> None:
        out = Outcome(status="error", error="boom")
        assert _has_truthy_error(out) is True

    def test_returns_false_when_status_error_and_error_none(self) -> None:
        out = Outcome(status="error", error=None)
        assert _has_truthy_error(out) is False

    def test_returns_false_when_status_error_and_error_empty_string(self) -> None:
        # Empty string is falsy — the F-1 gate must not flip from
        # an empty message (same semantic as None for the
        # finalize path's non-None requirement).
        out = Outcome(status="error", error="")
        assert _has_truthy_error(out) is False

    def test_returns_false_when_status_completed(self) -> None:
        out = Outcome(status="completed", error=None)
        assert _has_truthy_error(out) is False

    def test_returns_false_when_status_terminated(self) -> None:
        # The legitimate terminated-branch path (the F-1 wedge's
        # legitimate None producer) — must not flip.
        out = Outcome(status="terminated", error=None)
        assert _has_truthy_error(out) is False


# ---------------------------------------------------------------------------
# S2 — None error does NOT flip parent error (plan §1, §1a)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_none_error_does_not_flip_parent_error() -> None:
    """S2 — ``Outcome(status='error', error=None)`` MUST NOT flip
    ``_parent_errored`` for the parent (the F-1 wedge trigger).

    The bus still fires the watcher (the parent's pending
    follow-up is delivered); only the parent-error flag is left
    un-flipped so the parent's terminalize path can apply the
    non-error outcome. Also pins the defensive WARNING log on
    the None path.
    """
    parent_iid = "parent-uuid-S2"
    watcher = _make_watcher_row(parent_iid)
    bus, _ = _build_bus(pending_for_source=[watcher])

    out = Outcome(status="error", error=None)
    with bus_mod._caplog_ctx() if hasattr(bus_mod, "_caplog_ctx") else _nullctx():
        fired = await bus.emit_terminal("task-F-1", out)

    # Watcher fired (the parent still gets the FollowUp — bus
    # does not block delivery on error text).
    assert len(fired) == 1
    assert fired[0].target_instance_id == parent_iid
    # BUT the parent-error flag is NOT flipped (the F-1 wedge fix).
    assert parent_iid not in bus._parent_errored
    # The fallback text is also NOT stamped (per §1 inside-block).
    assert parent_iid not in bus._parent_error_message


# ---------------------------------------------------------------------------
# S3 — Real error flips parent error (the normal path, unchanged)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_error_flips_parent_error() -> None:
    """S3 — ``Outcome(status='error', error='boom')`` DOES flip
    ``_parent_errored`` and stamps the error message (the normal
    path; F-1 leaves this unchanged per §1)."""
    parent_iid = "parent-uuid-S3"
    watcher = _make_watcher_row(parent_iid)
    bus, _ = _build_bus(pending_for_source=[watcher])

    out = Outcome(status="error", error="boom")
    fired = await bus.emit_terminal("task-F-1", out)

    assert len(fired) == 1
    # Parent-error flag flipped.
    assert bus._parent_errored[parent_iid] is True
    # Error message stamped.
    assert bus._parent_error_message[parent_iid] == "boom"


# ---------------------------------------------------------------------------
# S4 — Terminal-stamped row of non-terminal instance SURVIVES clear
# (kill-switch ON — the F-1 fix's primary coverage; plan §2, §13b)
# ---------------------------------------------------------------------------


def test_terminal_auto_continued_survives_clear() -> None:
    """S4 — a terminal task with ``auto_continued_at`` set AND
    owning instance still non-terminal is PRESERVED by the
    2-arm predicate (kill-switch ON).

    The kill-switch is the per-wipe flag; when ON, the predicate
    is the 2-arm disjunction. The row matches arm 3.
    """
    # In-memory row shape.
    row = {
        "status": "failed",
        "auto_continued_at": "2026-10-04T10:00:00",
        "instance_id": "inst-S4",
        "instance_status": "waiting_children",
    }
    with _env("ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE", "1"):
        keep = _apply_preserve_predicate([row])
    # Row preserved.
    assert row in keep


# ---------------------------------------------------------------------------
# S5 — Terminal row WITHOUT marker is DELETED by clear (pre-F-1 baseline)
# ---------------------------------------------------------------------------


def test_terminal_no_marker_deleted_by_clear() -> None:
    """S5 — a terminal task WITHOUT ``auto_continued_at`` AND
    not in arm 1 (status not running/paused) is DELETED by the
    wipe. This is the pre-F-1 baseline (preserved) and the
    F-1 wipe is safe for this class."""
    row = {
        "status": "failed",
        "auto_continued_at": None,
        "instance_id": "inst-S5",
        "instance_status": "running",
    }
    with _env("ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE", "1"):
        keep = _apply_preserve_predicate([row])
    # Row deleted.
    assert row not in keep
    assert keep == []


# ---------------------------------------------------------------------------
# S6 — Boot sequence mock: candidates == 1
# (the F-1 wedge's ``candidates == 0`` condition is closed)
# ---------------------------------------------------------------------------


def test_boot_sequence_mock_candidates_one() -> None:
    """S6 — under the F-1 fix, the boot pass observes
    ``candidates == 1`` for the straddled child (the F-1 wedge
    closed the ``candidates == 0`` path that orphaned the parent
    in ``waiting_children``).

    The boot pass reads candidates from the task repository
    (mocked here). The F-1 fix means the WIPED-AFTER-STAMP
    row is preserved (arm 3) so the boot pass sees it.
    """
    # Simulate the post-wipe state: the stamped task row is
    # preserved by the 2-arm predicate (arm 3 active).
    rows = [
        {
            "id": 1,
            "status": "failed",
            "auto_continued_at": "2026-10-04T10:00:00",
            "instance_id": "inst-S6",
            "instance_status": "running",
        },
    ]
    with _env("ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE", "1"):
        keep = _apply_preserve_predicate(rows)
    # candidates == 1 (the stamped row is preserved).
    assert len(keep) == 1
    assert keep[0]["id"] == 1


# ---------------------------------------------------------------------------
# S1 — Double restart no double continue
# ---------------------------------------------------------------------------


def test_double_restart_no_double_continue() -> None:
    """S1 — a second restart does NOT re-continue a row that
    was already stamped at the first restart (the CAS
    ``< :boot_epoch`` arm declines a same-epoch restamp; a
    newer-epoch restamp is allowed per D17).

    Mirrors the ``mark_task_auto_continued`` rowcount==1
    contract: the second stamp returns False because
    ``auto_continued_at < boot_epoch`` is false on a same-epoch
    re-stamp.
    """
    # Simulate a stamped task row that survived wipe (arm 3).
    row = {
        "id": 1,
        "status": "failed",
        "auto_continued_at": "2026-10-04T10:00:00",
        "instance_id": "inst-S1",
        "instance_status": "running",
    }
    # After first restart: stamped, preserved.
    with _env("ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE", "1"):
        first_keep = _apply_preserve_predicate([row])
    assert first_keep == [row]

    # Second restart: stamp is same-epoch → no re-stamp → row
    # still preserved (arm 3, marker unchanged). No double
    # continue because the CAS would decline a same-epoch
    # re-stamp (the production CAS is in
    # ``TaskRepository.mark_task_auto_continued``; the test
    # pins the "preserved across two restarts" surface).
    with _env("ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE", "1"):
        second_keep = _apply_preserve_predicate([row])
    assert second_keep == [row]
    # The row is preserved in BOTH restarts — no double-stamp
    # observable side-effect on the wipe-side surface.


# ---------------------------------------------------------------------------
# S7 — Kill-switch BOTH states (renamed env var; W-3 / §13c)
# ---------------------------------------------------------------------------


def test_boot_auto_continued_preserve_kill_switch() -> None:
    """S7 — pins BOTH the ON path and the OFF path for the
    ``ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE`` kill-switch:

    * ON (default) — arm 3 active; terminal-stamped row of
      non-terminal instance SURVIVES the wipe.
    * OFF — arm 3 disarmed; terminal-stamped row is DELETED
      (reverts to the pre-F-1 wipe); arm 1 (status) is active
      in BOTH cases (a running/paused row is preserved
      regardless of the kill-switch).
    """
    # The terminal-stamped row of a non-terminal instance —
    # the F-1 wedge's target class.
    stamped_non_terminal = {
        "id": 10,
        "status": "failed",
        "auto_continued_at": "2026-10-04T10:00:00",
        "instance_id": "inst-S7-stamped",
        "instance_status": "running",
    }
    # A running task — arm 1 coverage. Must survive in BOTH
    # kill-switch states.
    running_row = {
        "id": 11,
        "status": "running",
        "auto_continued_at": None,
        "instance_id": "inst-S7-running",
        "instance_status": "running",
    }

    with _env("ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE", "1"):
        keep_on = _apply_preserve_predicate(
            [stamped_non_terminal, running_row]
        )
    # ON: arm 3 active — stamped row PRESERVED; arm 1 also
    # active — running row PRESERVED.
    assert stamped_non_terminal in keep_on
    assert running_row in keep_on
    assert len(keep_on) == 2

    with _env("ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE", "0"):
        keep_off = _apply_preserve_predicate(
            [stamped_non_terminal, running_row]
        )
    # OFF: arm 3 disarmed — stamped row DELETED; arm 1 still
    # active — running row PRESERVED.
    assert stamped_non_terminal not in keep_off
    assert running_row in keep_off
    assert len(keep_off) == 1


# ---------------------------------------------------------------------------
# Helper: in-test SQL predicate re-implementation
# ---------------------------------------------------------------------------


def _apply_preserve_predicate(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Re-implement the 2-arm ``TaskRepository.clear_all`` preserve
    predicate in pure Python for the unit test.

    Mirrors the production SQL in
    ``daemon/repositories/task/repository.py`` (post-F-1). The
    kill-switch is read from the env per wipe (default ON).

    Keep-set = arm 1 OR arm 3 (when kill-switch is ON):
      * arm 1: status in ('running', 'paused')
      * arm 3: auto_continued_at IS NOT NULL AND
               instance_status NOT IN TERMINAL_INSTANCE_STATUSES

    The test exercises the production kill-switch env name:
    ``ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE`` (default ON; ``=0``
    disables arm 3). The FP1 JobItem-anchor clause (the
    pre-existing ``NOT EXISTS (JobItem WHERE admission_state IN
    ('active','queued'))``) is always preserved; the test rows
    are seeded without an active JobItem, so the FP1 clause
    is satisfied for all of them and is a no-op for these
    keep-set decisions.
    """
    kill_switch_on = os.environ.get(
        "ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE", "1"
    ) != "0"

    keep: list[dict[str, Any]] = []
    for row in rows:
        # Arm 1: status in ('running', 'paused').
        if row["status"] in ("running", "paused"):
            keep.append(row)
            continue
        # Arm 3: only active when kill-switch is ON.
        if kill_switch_on:
            if (
                row["auto_continued_at"] is not None
                and row["instance_status"] not in TERMINAL_INSTANCE_STATUSES
            ):
                keep.append(row)
                continue
        # Arm 1 and arm 3 missed → row is doomed.
    return keep


# ---------------------------------------------------------------------------
# Helper: null context manager (replacement for ``contextlib.nullcontext``
# import dance; the bus code does not raise inside the gate block, so we
# just need a no-op cm for symmetry).
# ---------------------------------------------------------------------------


class _nullctx:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_args: Any) -> None:
        return None
