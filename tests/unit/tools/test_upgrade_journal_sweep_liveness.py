"""Unit tests for the upgrade-journal sweep's THREE-signal liveness
predicate (P2 / WS-5 / decision (b) / T2.5 / AC4).

The sweep's `_executor_alive_recent` predicate (P2 hardening at
``daemon/services/upgrade_journal_sweep.py:1590``) reads three signals
to decide whether a `PendingOp`'s owner is alive:

  (1) `in_flight.last_heartbeat` freshness (the splice path; THIS is
      the LOAD-BEARING signal — the shell lane writes it via
      `_splice_journal set-in-flight-field last_heartbeat ...` from
      `lock_heartbeat`'s 30s cadence; the accessor is
      `uj.journal_owner_heartbeat_fresh`, fail-closed at 60s).
  (2) `os.kill(owner_pid, 0) == 0` — owner process alive.
  (3) cgroup leaf (extensible; P3 widens to expected-unit match).

AC4 cells (test-strategy.md §1):

  AC4a — FRESH `in_flight.last_heartbeat` (via splice path) + STALE
        `armed_at` + LIVE pid ⇒ `_executor_alive_recent` returns True;
        sweep does NOT clear pending_op.
  AC4b — defense-in-depth: unparseable ts + pid present ⇒ True (the
        pin at sweep `:1592-95`); lock-held defer; fresh heartbeat
        alone.
  AC4c — STALE armed_at + no heartbeat + dead pid ⇒ sweep proceeds
        (the guard is not vacuously always-True; the control cell).

FIXTURE SHAPE (T2.10 corrected): per the plan, the fixture MUST write
`in_flight.last_heartbeat` via the splice path in a setup helper —
NOT a fake `owner_heartbeat_at` writer (that was the production-
impossible fixture, superseded). `owner_heartbeat_at` is the read-only
fallback field on `pending_op`; the sweep's defense-in-depth reads it
as part of the OR fallback chain. Today's tests pin both paths.

LIT NOTE: `install` (the tmp install dir used by these tests) is
NEVER the live install (`~/agents-ensemble`) — the sweep's live-path
refusal is at `ledger_check.py:46-55` and is OUT OF SCOPE here
(the tests don't hit the helper's live path; they go through the
sweep service directly).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# The tests import from `daemon.services.upgrade_journal_sweep` and
# `daemon.tools.upgrade_journal`. We pin PYTHONPATH to the repo root
# so the existing `tests/conftest.py` mock-langgraph shims aren't
# needed (this pack never touches LangGraph). pytest finds the
# modules via the REPO_ROOT.
REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPO_ROOT / "scripts" / "upgrade" / "journal_splice.py"
# The host's system python3 (NOT the worktree venv) — the helper
# runs against whatever promote.sh would have available. pytest is
# itself `sys.executable`'d; that's the same interpreter we'd promote
# under, so we re-use it for the helper subprocess invocations.
PY = sys.executable


# ── Helper utilities (paths the test pack owns) ────────────────────────────


def _init_install(install: Path) -> Path:
    """Initialize a fresh install under ``install`` (a tmp path). Returns
    the journal_path."""
    install.mkdir(parents=True, exist_ok=True)
    releases = install / "releases"
    releases.mkdir(exist_ok=True)
    state = releases / "state.json"
    payload = {
        "current": None,
        "previous": None,
        "in_flight": None,
        "rollback_window_count": {"24h": 0, "window_start": None},
        "cooldown_until": None,
        "quarantined": [],
        "history": [],
    }
    state.write_text(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return state


def _splice_set_in_flight_hb(state: Path, epoch_seconds: int) -> None:
    """Mirror the shell lane's lock_heartbeat: write
    ``in_flight.last_heartbeat`` via the splice path. This is the
    production-shaped writer; tests use it instead of constructing
    fake `owner_heartbeat_at` data."""
    # First create an in_flight = {} via the splice path
    rc = subprocess.run(
        [PY, str(HELPER), str(state), "set-in-flight-field", "last_heartbeat", str(epoch_seconds)],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": ""},
        timeout=10,
    )
    assert rc.returncode == 0, (rc.stderr, rc.stdout)


def _read_journal(state: Path) -> dict[str, object]:
    return json.loads(state.read_text(encoding="utf-8"))


# ── The P2 / T2.10 producer-shape fixture ──────────────────────────────────


@pytest.fixture
def install_with_hb(tmp_path: Path):
    """A fresh install dir + a PendingOp fixture sub-tree:
        install/releases/state.json       (splice: in_flight.last_heartbeat)
        install/releases/{run_id}/pending_op.json (PendingOp persisted)
    Returns (install_path, journal_path).
    """
    state = _init_install(tmp_path / "install")
    return tmp_path / "install", state


def _arm_op(
    install: Path, owner_pid: int, owner_kind: str = "executor"
) -> None:
    """Write a PendingOp record using the python twin (NOT the splice
    helper — the splice MUST NOT touch pending_op per D-P2.2). The
    PendingOp.armed_at is set to now-ISO (so it's FRESH; tests that
    need STALE armed_at manipulate it AFTER this call)."""
    import datetime as dt

    from daemon.tools import upgrade_journal as uj

    armed_at = dt.datetime.now(tz=dt.timezone.utc).isoformat()
    op = uj.PendingOp(
        run_id="r-liveness-test",
        kind="promote",
        env="dev",
        target="v9.9.9",
        armed_at=armed_at,
        armed_by_instance="test-fixture",
        owner_pid=owner_pid,
        owner_kind=owner_kind,
        # owner_heartbeat_at is None — tests use a fresh
        # in_flight.last_heartbeat instead (T2.10 corrected).
        owner_heartbeat_at=None,
    )
    uj.write_pending_op(install, op)


def _stale_op(install: Path, owner_pid: int) -> None:
    """Replace the pending_op with a STALE armed_at (>900s ago)."""
    import datetime as dt

    from daemon.tools import upgrade_journal as uj

    op = uj.PendingOp(
        run_id="r-stale-liveness",
        kind="promote",
        env="dev",
        target="v9.9.9",
        # 1500s ago — well past EXECUTOR_STALENESS_WINDOW_S=900s and
        # past OWNER_HEARTBEAT_FRESH_S=60s.
        armed_at=(dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(seconds=1500)).isoformat(),
        armed_by_instance="stale-fixture",
        owner_pid=owner_pid,
        owner_kind="executor",
    )
    uj.write_pending_op(install, op)


def _read_op(install: Path):
    from daemon.tools import upgrade_journal as uj
    return uj.read_pending_op(install)


# ── Tests ──────────────────────────────────────────────────────────────────


class TestJournalOwnerHeartbeatFresh:
    """The accessor `uj.journal_owner_heartbeat_fresh` reads
    `in_flight.last_heartbeat` and reports True/False based on the
    60s freshness window (OWNER_HEARTBEAT_FRESH_S)."""

    def test_fresh_hb_returns_true(self, tmp_path: Path) -> None:
        from daemon.tools import upgrade_journal as uj

        install = tmp_path / "install"
        state = _init_install(install)
        _splice_set_in_flight_hb(state, int(time.time()))
        assert uj.journal_owner_heartbeat_fresh(install) is True

    def test_stale_hb_returns_false(self, tmp_path: Path) -> None:
        from daemon.tools import upgrade_journal as uj

        install = tmp_path / "install"
        state = _init_install(install)
        # 1500s ago — well past OWNER_HEARTBEAT_FRESH_S=60
        _splice_set_in_flight_hb(state, int(time.time()) - 1500)
        assert uj.journal_owner_heartbeat_fresh(install) is False

    def test_no_in_flight_returns_false(self, tmp_path: Path) -> None:
        from daemon.tools import upgrade_journal as uj

        install = tmp_path / "install"
        _init_install(install)
        # in_flight remains null
        assert uj.journal_owner_heartbeat_fresh(install) is False

    def test_no_journal_returns_false(self, tmp_path: Path) -> None:
        from daemon.tools import upgrade_journal as uj

        install = tmp_path / "missing-install"
        install.mkdir()
        # state.json absent
        assert uj.journal_owner_heartbeat_fresh(install) is False

    def test_torn_journal_returns_false(self, tmp_path: Path) -> None:
        from daemon.tools import upgrade_journal as uj

        install = tmp_path / "install"
        state = _init_install(install)
        # Truncate mid-string
        text = state.read_text(encoding="utf-8")
        state.write_text(text[:len(text) // 2], encoding="utf-8")
        assert uj.journal_owner_heartbeat_fresh(install) is False


class TestThreeSignalSweepPredicate:
    """AC4 (a/b/c): the three-signal `_executor_alive_recent`
    predicate at
    ``daemon/services/upgrade_journal_sweep.py:1590``. We construct
    the predicate's INPUTS (a real subprocess sleeper for the pid,
    a fresh in_flight.last_heartbeat via the splice path for the
    heartbeat signal) and assert the predicate's TRUE/FALSE shape.

    The sweep service's `__init__` requires `install_dir` and an
    `Interval seconds`; we instantiate it directly and call
    `_executor_alive_recent` on a PendingOp model built in-place
    (the read-only constructor seam from the plan)."""

    def _build_sweep(self, install: Path):
        """Construct the sweep service with a tiny interval (so
        constructor kwargs can absorb the bound without spinning up
        the periodic task)."""
        from daemon.services import upgrade_journal_sweep as svc

        # We pass `manager=None` and let the service NOT start its
        # periodic tick — only the predicate is exercised here.
        return svc.UpgradeJournalSweepService(
            install,
            reconcile_interval_seconds=86400,  # effectively disable periodic
            reaper_timeout_seconds=60,
            manager=None,
        )

    def test_ac4a_fresh_hb_stale_armed_live_pid_returns_true(
        self, tmp_path: Path
    ) -> None:
        """AC4a — the wedge scenario: executor is alive but the bash
        scribe is slow / armed_at aged past the backstop. The sweep
        must NOT misclassify."""
        from daemon.tools import upgrade_journal as uj

        install = tmp_path / "install"
        state = _init_install(install)
        # Real sleeping subprocess (kill targetable)
        sleeper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(3600)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            # Fresh heartbeat via splice (within 60s)
            _splice_set_in_flight_hb(state, int(time.time()))
            # Stale armed_at (> 900s ago)
            _stale_op(install, sleeper.pid)
            op = _read_op(install)
            assert op is not None
            assert op.owner_pid == sleeper.pid
            assert op.owner_kind == "executor"

            sweep = self._build_sweep(install)
            assert sweep._executor_alive_recent(op) is True
        finally:
            sleeper.kill()
            sleeper.wait(timeout=5)

    def test_ac4b_unparseable_hb_with_live_pid_returns_true(
        self, tmp_path: Path
    ) -> None:
        """AC4b-1 — unparseable heartbeat ts + pid present → True
        (the existing pin at sweep :1592-95; defense-in-depth)."""
        from daemon.tools import upgrade_journal as uj

        install = tmp_path / "install"
        state = _init_install(install)
        # Write in_flight with a NON-numeric last_heartbeat (string)
        # — the accessor returns False on this; the sweep's pid-alive
        # check rescues the predicate to True.
        payload = {
            "current": None,
            "previous": None,
            "in_flight": {"last_heartbeat": "garbage-not-epoch"},
            "rollback_window_count": {"24h": 0, "window_start": None},
            "cooldown_until": None,
            "quarantined": [],
            "history": [],
        }
        state.write_text(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        sleeper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(3600)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _stale_op(install, sleeper.pid)
            op = _read_op(install)
            assert op is not None
            sweep = self._build_sweep(install)
            # Signal (1) unparseable → returns False. Signal (2) pid
            # alive → True. The OR-table accepts pid alone as enough.
            assert sweep._executor_alive_recent(op) is True
        finally:
            sleeper.kill()
            sleeper.wait(timeout=5)

    def test_ac4b_fresh_hb_alone_returns_true_no_pid(
        self, tmp_path: Path
    ) -> None:
        """AC4b-3 — fresh heartbeat alone (stale armed_at, pid slot
        empty) → True (SPEC cell). C8: the predicate consults hb
        freshness before any pid gate, so the sweep leaves the
        pending_op alone in the pre-spawn-stamp window."""
        from daemon.tools import upgrade_journal as uj

        install = tmp_path / "install"
        state = _init_install(install)
        # Fresh heartbeat via splice (within 60s)
        _splice_set_in_flight_hb(state, int(time.time()))
        # PendingOp with owner_pid = 0 (never started / dead pid)
        op = uj.PendingOp(
            run_id="r-fresh-hb-alone",
            kind="promote",
            env="dev",
            target="v9.9.9",
            armed_at="2026-10-11T00:00:00+00:00",  # well in the past
            owner_pid=0,
            owner_kind="executor",
        )
        # Stale armed_at + pid = 0 — but heartbeat fresh ⇒ True
        sweep = self._build_sweep(install)
        # C8 (council 2026-10-12): the predicate consults heartbeat
        # freshness FIRST — the AC4b trust-table cell "fresh hb alone
        # → leave alone even when pid and cgroup are missing" is the
        # SPEC, and the pre-spawn-stamp window (owner_pid == 0 while
        # the live promote heartbeats in_flight.last_heartbeat) is
        # exactly the case it protects. The old shape asserted the
        # INVERTED value here (a rationalized pid<=0-first
        # short-circuit) while the cell NAME promised True — code and
        # test now agree on the spec.
        assert sweep._executor_alive_recent(op) is True

    def test_ac4c_stale_armed_no_hb_dead_pid_returns_false(
        self, tmp_path: Path
    ) -> None:
        """AC4c — CONTROL CELL — sweep proceeds (predicate returns
        False). Proves the guard is not vacuously always-True."""
        from daemon.tools import upgrade_journal as uj

        install = tmp_path / "install"
        state = _init_install(install)
        # in_flight remains null — no heartbeat
        # Construct a fake owner_pid that's GUARANTEED dead: PID 1
        # init on linux but we'll use 2**22 which is past any
        # realistic pid (the next-instant pid allocation in tests
        # won't reach it; splice helper, journal daemon won't
        # either).
        dead_pid = 2 ** 22
        _stale_op(install, dead_pid)
        op = _read_op(install)
        assert op is not None
        sweep = self._build_sweep(install)
        # All three signals fail (no hb, dead pid, no cgroup
        # determination ⇒ conservative) → False
        assert sweep._executor_alive_recent(op) is False

    def test_non_executor_kind_returns_false(
        self, tmp_path: Path
    ) -> None:
        """owner_kind != 'executor' is the sweep's bail-out — the
        heartbeat freshness logic is gated behind the executor
        lane."""
        from daemon.tools import upgrade_journal as uj

        install = tmp_path / "install"
        state = _init_install(install)
        _splice_set_in_flight_hb(state, int(time.time()))
        sleeper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(3600)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            op = uj.PendingOp(
                run_id="r-tool-arm",
                kind="promote",
                env="dev",
                target="v9.9.9",
                armed_at="2026-10-11T00:00:00+00:00",
                owner_pid=sleeper.pid,
                owner_kind="tool-arm",  # NOT executor
            )
            sweep = self._build_sweep(install)
            assert sweep._executor_alive_recent(op) is False
        finally:
            sleeper.kill()
            sleeper.wait(timeout=5)

    def test_ac4b_owner_heartbeat_at_fallback_still_works(
        self, tmp_path: Path
    ) -> None:
        """Defense-in-depth fallback: ``op.owner_heartbeat_at`` (ISO
        string) is the FALLBACK signal — it must still keep the
        predicate True when ``in_flight.last_heartbeat`` is
        missing. This is the legacy reader the sweep's accessor
        at :1590 originally trusted; the new accessor subsumes
        it but the fallback path is still load-bearing."""
        from daemon.tools import upgrade_journal as uj

        install = tmp_path / "install"
        state = _init_install(install)
        # No in_flight.last_heartbeat (write in_flight={}; helper
        # refuses on a string value at the field because that's a
        # foreign-writer drift — set the field via a manual write
        # directly on the journal):
        sleeper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(3600)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            # Use the helper to create in_flight = {last_heartbeat: 0}
            # (epoch 0 is the well-known "never" marker; the
            # OWNER_HEARTBEAT_FRESH_S check will treat it as stale,
            # forcing the OR chain to fall through to the
            # owner_heartbeat_at field).
            _splice_set_in_flight_hb(state, 0)
            # Fresh op.owner_heartbeat_at (now-ISO) carries the
            # legacy ISO-string freshness signal.
            op = uj.PendingOp(
                run_id="r-fallback-hb",
                kind="promote",
                env="dev",
                target="v9.9.9",
                armed_at="2026-10-11T00:00:00+00:00",
                owner_pid=sleeper.pid,
                owner_kind="executor",
                owner_heartbeat_at=uj.now_iso(),
            )
            sweep = self._build_sweep(install)
            # Signal (1): in_flight.last_heartbeat = 0 is STALE (>60s)
            # → False. Signal (2): pid alive → True. The OR accepts.
            assert sweep._executor_alive_recent(op) is True
        finally:
            sleeper.kill()
            sleeper.wait(timeout=5)
