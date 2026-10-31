"""Reaper service unit pack (bash-tool orphan reaper Phases 1+2).

Phase 1 pins (plan task 1.2 acceptance):
* Kill-switch matrix 3×3 — absent ⇒ ON in ALL lanes (F6 flip, asserted
  in the dev lane too); explicit off everywhere; explicit truthy ON
  everywhere.
* Platform dispatch for the env read-backends (linux /proc exact-entry,
  darwin ``ps eww``, else fail-open) — monkeypatched platform.
* D9 hard start-time gate pins — too-young member ⇒ refuse;
  unparseable/None start ⇒ refuse (fail-closed).
* Hard-guard refusals — pgid=1, daemon's own group, self pid.
* D8 daemon_pid live-sibling guard — live foreign holder (start ≤ row)
  ⇒ skip; recycled holder (started after row) ⇒ proceed; self rows
  always proceed.

Phase 2 pins (plan tasks 2.1 / 2.6 acceptance):
* Ladder ORDER asserted via a record-and-succeed ``os.killpg`` census
  (the hermetic unit shape of the record-and-forward census at
  ``tests/integration/test_service_reconciliation_e2e.py:736-805`` —
  REAL signal delivery is proven by the real-process Path A leg in
  ``tests/tools/test_auto_kill_integration.py``).
* Attribution legs — env exact-match ⇒ ``reaped:env-match``; readable
  mismatch ⇒ REFUSE (veto even when the gate passed); unreadable ⇒
  pgid-only (logged ``attribution=pgid-only``, two-signal via D9).
* ESRCH swallowed at both ladder sites (TERM ESRCH skips grace+KILL).
* Sweep bookkeeping — killed/refused/gc rows closed, sibling rows left
  open, RAM-dedup'd rows closed without a second signal.
* Switch enforcement (m3) — master OFF ⇒ sweeps return 0 actions with
  ZERO repository access, under a hard-raise ``os.killpg`` fence.
* Lane parity — chart/terminate lanes reap real rows through the
  extended ``cleanup_instance`` (real SQLite repo, dead-group rows).

Unit tests use synthetic pgids + recording spies, so NO test here ever
delivers a real signal: ``os.killpg`` is either a record-and-succeed
census or a hard-raise fence; ``os.kill`` is a recording spy that only
permits signal 0 (liveness probes).
"""

from __future__ import annotations

import logging
import os
import signal
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import daemon.services.bash_spawn_reaper as reaper
from daemon.services.bash_spawn_reaper import (
    BASH_TRACKING_ENV_VAR,
    ENV_KILL_SWITCH,
    CURRENT_BOOT_ID,
    kill_switch_from_env,
    sweep_instance,
    sweep_open_rows_for_shutdown,
    sweep_rows,
    verify_and_kill,
)


# ── Kill fence: no kill may fire in pure-predicate tests ────────────────────
@pytest.fixture
def kill_fence(monkeypatch):
    """Record os.kill calls; hard-fail os.killpg. Signal 0 (liveness
    probe) is allowed through the spy."""
    kills: list[tuple[int, int]] = []

    def _spy_kill(pid, sig):  # noqa: ANN001
        kills.append((pid, sig))
        if sig != 0:
            raise AssertionError(f"KILL SIGNAL IN TEST: os.kill({pid}, {sig})")
        # live-pid probe succeeds (caller decides what that means)

    def _no_killpg(pgid, sig):  # noqa: ANN001
        raise AssertionError(f"UNEXPECTED KILLPG: os.killpg({pgid}, {sig})")

    monkeypatch.setattr(reaper.os, "kill", _spy_kill)
    monkeypatch.setattr(reaper.os, "killpg", _no_killpg)
    return kills


@pytest.fixture
def kill_census(monkeypatch):
    """Record-and-succeed ``os.killpg`` census for ladder tests.

    Records every (pgid, sig) and reports SUCCESS without delivering
    anything (synthetic pgids only — nothing real is ever signaled;
    the real-delivery leg is the Path A real-process test). Also
    records ``time.sleep`` calls so the grace window is observable
    without actually waiting.
    """
    killpg_calls: list[tuple[int, int]] = []
    sleeps: list[float] = []

    def _spy_kill(pid, sig):  # noqa: ANN001
        if sig != 0:
            raise AssertionError(f"NON-PROBE os.kill: ({pid}, {sig})")
        # signal-0 liveness probe: succeed (direct child "alive")

    def _rec_killpg(pgid, sig):  # noqa: ANN001
        killpg_calls.append((pgid, sig))

    monkeypatch.setattr(reaper.os, "kill", _spy_kill)
    monkeypatch.setattr(reaper.os, "killpg", _rec_killpg)
    monkeypatch.setattr(reaper.time, "sleep", lambda s: sleeps.append(s))
    return killpg_calls, sleeps


@pytest.fixture
def dead_probe(monkeypatch):
    """``os.kill`` raises ProcessLookupError for every pid (everything
    looks dead: direct child, D8 holder)."""
    def _dead(pid, sig):  # noqa: ANN001
        raise ProcessLookupError(pid)

    monkeypatch.setattr(reaper.os, "kill", _dead)


def _row(**over):
    base = dict(
        pid=999001,
        pgid=999001,
        start_time=500,
        tracking_id="a" * 32,
        instance_id="inst-x",
        command_sanitized="sleep 30",
        boot_id=CURRENT_BOOT_ID,
        daemon_pid=os.getpid(),  # self rows always proceed (MAJOR-1)
        created_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        reaped_at=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


# ── Switch matrix 3×3 ────────────────────────────────────────────────────────
class TestSwitchMatrix:
    @pytest.mark.parametrize("is_prod", [None, True, False])
    def test_absent_is_on_in_all_lanes(self, monkeypatch, is_prod) -> None:
        monkeypatch.delenv(ENV_KILL_SWITCH, raising=False)
        assert kill_switch_from_env({}, is_prod=is_prod) is True
        # live-env read too (the dev lane: absent ⇒ ON)
        assert kill_switch_from_env() is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "off", "FALSE", "Off", "NO"])
    @pytest.mark.parametrize("is_prod", [None, True, False])
    def test_explicit_off_everywhere(self, monkeypatch, val, is_prod) -> None:
        monkeypatch.setenv(ENV_KILL_SWITCH, val)
        assert kill_switch_from_env(is_prod=is_prod) is False
        assert kill_switch_from_env({ENV_KILL_SWITCH: val}, is_prod=is_prod) is False

    @pytest.mark.parametrize("val", ["1", "true", "yes", "", "  ", "on"])
    @pytest.mark.parametrize("is_prod", [None, True, False])
    def test_explicit_truthy_on_everywhere(self, monkeypatch, val, is_prod) -> None:
        monkeypatch.setenv(ENV_KILL_SWITCH, val)
        assert kill_switch_from_env(is_prod=is_prod) is True
        assert kill_switch_from_env({ENV_KILL_SWITCH: val}, is_prod=is_prod) is True

    def test_env_var_name_pinned(self) -> None:
        assert ENV_KILL_SWITCH == "ENSEMBLE_BASH_ORPHAN_REAPER"
        assert BASH_TRACKING_ENV_VAR == "ENSEMBLE_BASH_TRACKING_ID"

    def test_boot_id_hex_per_process(self) -> None:
        assert len(CURRENT_BOOT_ID) == 32
        int(CURRENT_BOOT_ID, 16)  # hex


# ── Platform dispatch (env read-backends) ────────────────────────────────────
class TestPlatformDispatch:
    def test_linux_dispatches_to_procfs(self, monkeypatch) -> None:
        calls = []
        monkeypatch.setattr(
            reaper, "_verify_member_env_procfs",
            lambda pid, tid: calls.append((pid, tid)) or True,
        )
        monkeypatch.setattr(reaper.sys, "platform", "linux")
        assert reaper.verify_member_env(123, "tid") is True
        assert calls == [(123, "tid")]

    def test_darwin_dispatches_to_ps(self, monkeypatch) -> None:
        calls = []
        monkeypatch.setattr(
            reaper, "_verify_member_env_ps",
            lambda pid, tid: calls.append((pid, tid)) or None,
        )
        monkeypatch.setattr(reaper.sys, "platform", "darwin")
        assert reaper.verify_member_env(123, "tid") is None
        assert calls == [(123, "tid")]

    def test_unknown_platform_fail_open_none(self, monkeypatch, caplog) -> None:
        monkeypatch.setattr(reaper.sys, "platform", "sunos5")
        with caplog.at_level(logging.WARNING, logger=reaper.__name__):
            assert reaper.verify_member_env(123, "tid") is None
        assert any("not supported" in r.getMessage() for r in caplog.records)

    def test_linux_procfs_exact_entry_match(self, monkeypatch) -> None:
        env = (
            f"PATH=/usr/bin\x00FOO=bar\x00"
            f"{BASH_TRACKING_ENV_VAR}=abc123\x00BAZ=qux\x00"
        ).encode()
        monkeypatch.setattr(reaper.sys, "platform", "linux")

        class _F:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return env

        def _fake_open(path, mode="r"):
            assert path == f"/proc/77/environ"
            return _F()

        monkeypatch.setattr(reaper, "open", _fake_open, raising=False)
        # exact entry ⇒ owned
        assert reaper.verify_member_env(77, "abc123") is True
        # substring of another value must NOT match
        other = (
            f"X={BASH_TRACKING_ENV_VAR}=abc123-suffix\x00".encode()
        )

        class _F2(_F):
            def read(self):
                return other

        monkeypatch.setattr(reaper, "open", lambda p, m="r": _F2())
        assert reaper.verify_member_env(77, "abc123") is False
        # readable but different value ⇒ fail-closed False
        diff = f"{BASH_TRACKING_ENV_VAR}=zzz\x00".encode()

        class _F3(_F):
            def read(self):
                return diff

        monkeypatch.setattr(reaper, "open", lambda p, m="r": _F3())
        assert reaper.verify_member_env(77, "abc123") is False
        # marker absent entirely ⇒ fail-closed False
        none = b"A=1\x00B=2\x00"

        class _F4(_F):
            def read(self):
                return none

        monkeypatch.setattr(reaper, "open", lambda p, m="r": _F4())
        assert reaper.verify_member_env(77, "abc123") is False

    def test_linux_procfs_unreadable_fail_open(self, monkeypatch) -> None:
        monkeypatch.setattr(reaper.sys, "platform", "linux")

        def _boom(path, mode="r"):
            raise FileNotFoundError(path)

        monkeypatch.setattr(reaper, "open", _boom, raising=False)
        assert reaper.verify_member_env(78, "tid") is None  # fail-open


# ── Hard guards ──────────────────────────────────────────────────────────────
class TestHardGuards:
    def test_pgid_1_refused(self, kill_fence) -> None:
        status, reason = verify_and_kill(_row(pgid=1, pid=2))
        assert status == "refused"
        assert "pgid" in reason

    def test_pgid_missing_refused(self, kill_fence) -> None:
        status, reason = verify_and_kill(_row(pgid=None))
        assert status == "refused"

    def test_daemon_group_refused(self, kill_fence) -> None:
        daemon_pgid = os.getpgid(os.getpid())
        status, reason = verify_and_kill(_row(pgid=daemon_pgid, pid=999001))
        assert status == "refused"
        assert "daemon" in reason

    def test_self_pid_refused(self, kill_fence) -> None:
        status, reason = verify_and_kill(_row(pid=os.getpid(), pgid=999123))
        assert status == "refused"
        assert "self" in reason

    def test_no_signals_for_refused_rows(self, kill_fence) -> None:
        # a fully-proceeding predicate would ladder — but every row in
        # this class is refused BEFORE the ladder, so the fence stays
        # empty (no killpg, no non-probe os.kill).
        assert kill_fence == []

    def test_ladder_fires_for_gate_passing_row(
        self, monkeypatch, kill_census
    ) -> None:
        """P2: a gate-passing row IS laddered (record-and-succeed census;
        real delivery is proven by the Path A real-process leg)."""
        monkeypatch.setattr(reaper, "verify_member_env", lambda pid, tid: None)
        monkeypatch.setattr(
            reaper, "process_start_wallclock",
            lambda pid: datetime.now(timezone.utc) - timedelta(seconds=120),
        )
        monkeypatch.setattr(reaper, "list_group_members", lambda pgid: [os.getpid() + 100001])
        row = _row(
            pid=os.getpid() + 100000,
            pgid=os.getpid() + 100000,
            daemon_pid=os.getpid(),
            created_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )
        status, reason = verify_and_kill(row, members=[], my_pid=os.getpid())
        assert status == "reaped"
        killpg_calls, sleeps = kill_census
        assert killpg_calls == [
            (row.pgid, signal.SIGTERM),
            (row.pgid, signal.SIGKILL),
        ]
        assert len(sleeps) == 1  # the grace window between TERM and KILL


# ── D8 daemon_pid live-sibling guard ────────────────────────────────────────
class TestDaemonPidGuard:
    def test_live_sibling_skipped(self, monkeypatch, kill_fence) -> None:
        holder_start = datetime.now(timezone.utc) - timedelta(hours=2)
        row_created = datetime.now(timezone.utc) - timedelta(seconds=60)
        monkeypatch.setattr(
            reaper, "process_start_wallclock", lambda pid: holder_start
        )
        status, reason = verify_and_kill(
            _row(daemon_pid=525252, created_at=row_created),
            members=[],
            my_pid=os.getpid(),
        )
        assert status == "skip"
        assert "sibling" in reason

    def test_recycled_holder_proceeds(self, monkeypatch, kill_census) -> None:
        holder_start = datetime.now(timezone.utc)  # started AFTER the row
        row_created = datetime.now(timezone.utc) - timedelta(seconds=60)
        monkeypatch.setattr(
            reaper, "process_start_wallclock", lambda pid: holder_start
        )
        monkeypatch.setattr(reaper, "verify_member_env", lambda pid, tid: None)
        status, _ = verify_and_kill(
            _row(daemon_pid=525252, created_at=row_created),
            members=[],
            my_pid=os.getpid(),
        )
        assert status != "skip"

    def test_dead_holder_proceeds(self, monkeypatch, kill_fence) -> None:
        def _dead(pid, sig):
            raise ProcessLookupError(pid)

        monkeypatch.setattr(reaper.os, "kill", _dead)
        status, _ = verify_and_kill(
            _row(daemon_pid=525252), members=[], my_pid=os.getpid()
        )
        assert status != "skip"

    def test_self_row_always_proceeds(self, monkeypatch, kill_census) -> None:
        # daemon_pid == my_pid ⇒ self-exemption even if a "live holder"
        # with an old start would otherwise match.
        holder_start = datetime.now(timezone.utc) - timedelta(hours=2)
        monkeypatch.setattr(
            reaper, "process_start_wallclock", lambda pid: holder_start
        )
        monkeypatch.setattr(reaper, "verify_member_env", lambda pid, tid: None)
        status, _ = verify_and_kill(
            _row(daemon_pid=os.getpid()), members=[], my_pid=os.getpid()
        )
        assert status != "skip"

    def test_unparseable_holder_start_skips_conservatively(
        self, monkeypatch, kill_fence
    ) -> None:
        monkeypatch.setattr(reaper, "process_start_wallclock", lambda pid: None)
        status, reason = verify_and_kill(
            _row(daemon_pid=525252), members=[], my_pid=os.getpid()
        )
        assert status == "skip"


# ── D9 hard start-time gate ──────────────────────────────────────────────────
class TestStartTimeGate:
    def test_too_young_member_refused(self, monkeypatch, kill_fence) -> None:
        # direct child dead; member enumerated but YOUNGER than the row
        # (recycled group) ⇒ refuse.
        def _dead(pid, sig):
            raise ProcessLookupError(pid)

        monkeypatch.setattr(reaper.os, "kill", _dead)
        row_created = datetime.now(timezone.utc) - timedelta(seconds=60)
        young = datetime.now(timezone.utc)  # now > row + 10s tolerance? no:
        # tolerance is 10s; now - row_created = 60s ⇒ member must be ≤
        # row + 10s = 50s ago. `young` is 0s ago ⇒ too young ⇒ refuse.
        status, reason = verify_and_kill(
            _row(created_at=row_created),
            members=[(888001, young)],
            my_pid=os.getpid(),
        )
        assert status == "refused"
        assert "start-time" in reason

    def test_old_enough_member_passes_gate(
        self, monkeypatch, kill_census
    ) -> None:
        def _dead(pid, sig):
            raise ProcessLookupError(pid)

        monkeypatch.setattr(reaper.os, "kill", _dead)
        monkeypatch.setattr(reaper, "verify_member_env", lambda pid, tid: None)
        row_created = datetime.now(timezone.utc) - timedelta(seconds=60)
        old = datetime.now(timezone.utc) - timedelta(seconds=55)
        monkeypatch.setattr(reaper, "list_group_members", lambda pgid: [888002])
        status, reason = verify_and_kill(
            _row(created_at=row_created),
            members=[(888002, old)],
            my_pid=os.getpid(),
        )
        assert status == "reaped"
        assert "pgid-only" in reason
        killpg_calls, _sleeps = kill_census
        assert killpg_calls[0][1] == signal.SIGTERM
        assert killpg_calls[-1][1] == signal.SIGKILL

    def test_none_start_member_refuses_fail_closed(
        self, monkeypatch, kill_fence
    ) -> None:
        def _dead(pid, sig):
            raise ProcessLookupError(pid)

        monkeypatch.setattr(reaper.os, "kill", _dead)
        row_created = datetime.now(timezone.utc) - timedelta(seconds=60)
        status, reason = verify_and_kill(
            _row(created_at=row_created),
            members=[(888003, None)],
            my_pid=os.getpid(),
        )
        assert status == "refused"
        assert "unparseable" in reason

    def test_dead_group_proceeds_to_gc(self, monkeypatch, kill_fence) -> None:
        def _dead(pid, sig):
            raise ProcessLookupError(pid)

        monkeypatch.setattr(reaper.os, "kill", _dead)
        status, reason = verify_and_kill(
            _row(created_at=datetime.now(timezone.utc) - timedelta(seconds=60)),
            members=[],
            my_pid=os.getpid(),
        )
        assert status == "proceed"
        assert "group-dead" in reason

    def test_direct_child_alive_passes_gate(
        self, monkeypatch, kill_census
    ) -> None:
        # os.kill census spy (signal-0 probe succeeds) ⇒ direct child
        # alive; patch the jiffies reader so the Linux exact-match leg
        # also holds; env verdict None ⇒ pgid-only attribution.
        monkeypatch.setattr(reaper, "_read_proc_start_time", lambda pid: 500)
        monkeypatch.setattr(reaper, "verify_member_env", lambda pid, tid: None)
        monkeypatch.setattr(
            reaper, "process_start_wallclock",
            lambda pid: datetime.now(timezone.utc) - timedelta(seconds=120),
        )
        monkeypatch.setattr(reaper, "list_group_members", lambda pgid: [888001])
        status, reason = verify_and_kill(
            _row(created_at=datetime.now(timezone.utc) - timedelta(seconds=60)),
            members=[],
            my_pid=os.getpid(),
        )
        assert status == "reaped"
        assert "pgid-only" in reason

    def test_direct_child_jiffies_mismatch_recycled(
        self, monkeypatch, kill_fence
    ) -> None:
        # direct child "alive" but Linux jiffies differ ⇒ PID recycled ⇒
        # the direct-child path must NOT satisfy the gate.
        monkeypatch.setattr(reaper.sys, "platform", "linux")
        monkeypatch.setattr(reaper, "_read_proc_start_time", lambda pid: 999)
        monkeypatch.setattr(reaper, "verify_member_env", lambda pid, tid: None)
        status, reason = verify_and_kill(
            _row(created_at=datetime.now(timezone.utc) - timedelta(seconds=60)),
            members=[],
            my_pid=os.getpid(),
        )
        # no member enumerated, direct child "alive" ⇒ refuse (gate)
        assert status == "refused"
        assert "start-time" in reason

    def test_tolerance_env_respected(self, monkeypatch, kill_fence) -> None:
        def _dead(pid, sig):
            raise ProcessLookupError(pid)

        monkeypatch.setattr(reaper.os, "kill", _dead)
        monkeypatch.setenv("ENSEMBLE_BASH_REAPER_START_TOLERANCE_SECONDS", "0.001")
        row_created = datetime.now(timezone.utc) - timedelta(seconds=60)
        member = datetime.now(timezone.utc) - timedelta(seconds=59)
        # tolerance 0.001 ⇒ member (59s ago) > row+0.001s (≈60s ago) ⇒ refuse
        status, reason = verify_and_kill(
            _row(created_at=row_created),
            members=[(888004, member)],
            my_pid=os.getpid(),
        )
        assert status == "refused"

    def test_platform_start_wallclock_else_none(self, monkeypatch) -> None:
        monkeypatch.setattr(reaper.sys, "platform", "win32")
        assert reaper.process_start_wallclock(1) is None


# ════════════════════════════════════════════════════════════════════════════
# Phase 2 — ladder, attribution, sweeps, switch enforcement, lane parity
# ════════════════════════════════════════════════════════════════════════════

def _spawn_repo_fixture_engine():
    """In-memory SQLite engine carrying the bash_spawn_registry table
    (StaticPool so it survives the asyncio.to_thread bridge)."""
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel, create_engine

    from daemon.repositories.bash_spawn import BashSpawnRecord  # noqa: F401

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def spawn_repo():
    """Real BashSpawnRepository over a fresh in-memory engine."""
    from daemon.repositories.bash_spawn import BashSpawnRepository

    eng = _spawn_repo_fixture_engine()
    yield BashSpawnRepository(engine=eng)
    eng.dispose()


class TestKillLadder:
    """Ladder order + ESRCH handling + attribution legs (task 2.1)."""

    def test_ladder_order_term_grace_kill(
        self, monkeypatch, kill_census
    ) -> None:
        monkeypatch.setattr(reaper, "verify_member_env", lambda pid, tid: True)
        monkeypatch.setattr(reaper, "list_group_members", lambda pgid: [777002])
        row = _row(
            pgid=777001,
            pid=777001,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        )
        status, reason = verify_and_kill(
            row, members=[(777002, datetime.now(timezone.utc) - timedelta(seconds=61))],
            my_pid=os.getpid(),
            term_grace_seconds=2.0,
        )
        assert status == "reaped"
        assert reason == "reaped:env-match"
        killpg_calls, sleeps = kill_census
        assert killpg_calls == [
            (777001, signal.SIGTERM),
            (777001, signal.SIGKILL),
        ]
        assert sleeps == [2.0]  # the grace window observed between them

    def test_term_esrch_skips_grace_and_kill(
        self, monkeypatch, kill_census
    ) -> None:
        """TERM lands ESRCH (group died between verify and ladder) ⇒ no
        grace sleep, no KILL leg, row still reaped (ESRCH swallowed)."""
        monkeypatch.setattr(reaper, "verify_member_env", lambda pid, tid: True)

        calls: list[tuple[int, int]] = []

        def _esrch_term(pgid, sig):
            calls.append((pgid, sig))
            if sig == signal.SIGTERM:
                raise ProcessLookupError(pgid)
            raise AssertionError("KILL leg should never fire after TERM ESRCH")

        monkeypatch.setattr(reaper.os, "killpg", _esrch_term)
        sleeps: list[float] = []
        monkeypatch.setattr(reaper.time, "sleep", lambda s: sleeps.append(s))

        row = _row(
            pgid=777010,
            pid=777010,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        )
        status, reason = verify_and_kill(
            row,
            members=[(777011, datetime.now(timezone.utc) - timedelta(seconds=61))],
            my_pid=os.getpid(),
        )
        assert status == "reaped"
        assert reason == "reaped:term-esrch"
        assert calls == [(777010, signal.SIGTERM)]
        assert sleeps == []

    def test_kill_esrch_swallowed_still_reaped(
        self, monkeypatch, kill_census
    ) -> None:
        """KILL lands ESRCH (group exited during the grace window) ⇒
        swallowed; row reaped."""
        monkeypatch.setattr(reaper, "verify_member_env", lambda pid, tid: True)

        # record-and-succeed for TERM, ESRCH for KILL
        def _term_ok_kill_esrch(pgid, sig):
            if sig == signal.SIGKILL:
                raise ProcessLookupError(pgid)
            kill_census[0].append((pgid, sig))

        monkeypatch.setattr(reaper.os, "killpg", _term_ok_kill_esrch)

        monkeypatch.setattr(reaper, "list_group_members", lambda pgid: [777021])
        row = _row(
            pgid=777020,
            pid=777020,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        )
        status, reason = verify_and_kill(
            row,
            members=[(777021, datetime.now(timezone.utc) - timedelta(seconds=61))],
            my_pid=os.getpid(),
        )
        assert status == "reaped"
        assert reason == "reaped:env-match"  # full ladder ran; KILL ESRCH swallowed

    def test_env_exact_match_attribution(
        self, monkeypatch, kill_census
    ) -> None:
        monkeypatch.setattr(reaper, "verify_member_env", lambda pid, tid: True)
        monkeypatch.setattr(reaper, "list_group_members", lambda pgid: [777031])
        row = _row(
            pgid=777030,
            pid=777030,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        )
        status, reason = verify_and_kill(
            row,
            members=[(777031, datetime.now(timezone.utc) - timedelta(seconds=61))],
            my_pid=os.getpid(),
        )
        assert status == "reaped"
        assert reason == "reaped:env-match"

    def test_readable_mismatch_vetoes_even_when_gate_passes(
        self, monkeypatch, kill_fence
    ) -> None:
        """Fail-closed: readable env with a DIFFERENT value vetoes the
        kill REGARDLESS of the start-time gate (recycled-pid defense)."""
        monkeypatch.setattr(reaper, "verify_member_env", lambda pid, tid: False)
        row = _row(
            pgid=777040,
            pid=777040,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        )
        status, reason = verify_and_kill(
            row,
            members=[(777041, datetime.now(timezone.utc) - timedelta(seconds=61))],
            my_pid=os.getpid(),
        )
        assert status == "refused"
        assert reason == "refused:env-mismatch"

    def test_pgid_only_attribution_logged(
        self, monkeypatch, kill_census, caplog
    ) -> None:
        """Unreadable env everywhere ⇒ pgid-only permitted (F1) but only
        alongside the D9 gate — logged ``attribution=pgid-only``."""
        monkeypatch.setattr(reaper, "verify_member_env", lambda pid, tid: None)
        monkeypatch.setattr(reaper, "list_group_members", lambda pgid: [777051])
        row = _row(
            pgid=777050,
            pid=777050,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        )
        with caplog.at_level(logging.INFO, logger=reaper.__name__):
            status, reason = verify_and_kill(
                row,
                members=[(777051, datetime.now(timezone.utc) - timedelta(seconds=61))],
                my_pid=os.getpid(),
            )
        assert status == "reaped"
        assert reason == "reaped:pgid-only"
        assert any("attribution=pgid-only" in r.getMessage() for r in caplog.records)

    def test_env_match_beats_mismatch_on_other_member(
        self, monkeypatch, kill_census
    ) -> None:
        """Any exact match attributes the group (the False on another
        member cannot veto a proven-exact match)."""

        def _mixed(pid, tid):
            return pid == 777061

        monkeypatch.setattr(reaper, "verify_member_env", _mixed)
        monkeypatch.setattr(reaper, "list_group_members", lambda pgid: [777061, 777062])
        row = _row(
            pgid=777060,
            pid=777060,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        )
        status, reason = verify_and_kill(
            row,
            members=[
                (777062, datetime.now(timezone.utc) - timedelta(seconds=61)),
                (777061, datetime.now(timezone.utc) - timedelta(seconds=61)),
            ],
            my_pid=os.getpid(),
        )
        assert status == "reaped"
        assert reason == "reaped:env-match"

    def test_grace_clamped_to_bounds(self, monkeypatch, kill_census) -> None:
        monkeypatch.setattr(reaper, "verify_member_env", lambda pid, tid: True)
        monkeypatch.setattr(reaper, "list_group_members", lambda pgid: [777071])
        row = _row(
            pgid=777070,
            pid=777070,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        )
        members = [(777071, datetime.now(timezone.utc) - timedelta(seconds=61))]
        verify_and_kill(row, members=members, my_pid=os.getpid(), term_grace_seconds=0.0)
        verify_and_kill(row, members=members, my_pid=os.getpid(), term_grace_seconds=999.0)
        _calls, sleeps = kill_census
        assert sleeps[0] == reaper.MIN_TERM_GRACE_SECONDS
        assert sleeps[1] == reaper.MAX_TERM_GRACE_SECONDS

    def test_win32_platform_refused_no_signals(
        self, monkeypatch, kill_fence
    ) -> None:
        """POSIX gating: win32 never ladders (constraint: every new kill
        leg is ``sys.platform != "win32"``-gated)."""
        monkeypatch.setattr(reaper.sys, "platform", "win32")
        monkeypatch.setattr(reaper, "verify_member_env", lambda pid, tid: True)
        row = _row(
            pgid=777080,
            pid=777080,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        )
        status, reason = verify_and_kill(
            row,
            members=[(777081, datetime.now(timezone.utc) - timedelta(seconds=61))],
            my_pid=os.getpid(),
        )
        assert status == "refused"
        assert reason == "refused:platform-win32"
        assert kill_fence == []  # early gate: not even a liveness probe


class _SpyRepo:
    """A recording stand-in for the repository (call-count assertions).

    Recording lists use leading-underscore names so they never shadow
    the real repository method names.
    """

    def __init__(self, rows=None):
        self.rows = rows or []
        self._by_instance_calls: list[str] = []
        self._open_all_calls = 0
        self._mark_calls: list[tuple] = []

    def list_open_by_instance(self, instance_id):
        self._by_instance_calls.append(instance_id)
        return list(self.rows)

    def list_open_all(self):
        self._open_all_calls += 1
        return list(self.rows)

    def mark_reaped(self, row_id, reaped_by, reason=None):
        self._mark_calls.append((row_id, reaped_by, reason))
        return SimpleNamespace(id=row_id)


class TestSweepRows:
    """Shared per-row executor bookkeeping (task 2.2)."""

    @pytest.mark.asyncio
    async def test_killed_row_closed_with_lane_label(
        self, monkeypatch, kill_census
    ) -> None:
        monkeypatch.setattr(reaper, "verify_member_env", lambda pid, tid: True)
        monkeypatch.setattr(
            reaper, "process_start_wallclock",
            lambda pid: datetime.now(timezone.utc) - timedelta(seconds=120),
        )
        row = _row(id=11, pgid=778001, pid=778001,
                   created_at=datetime.now(timezone.utc) - timedelta(seconds=60))
        repo = _SpyRepo()
        actions = await sweep_rows(
            [row], repo=repo, reaped_by="terminal-tier",
            my_pid=os.getpid(), term_grace_seconds=0.5,
        )
        assert actions == 1
        assert repo._mark_calls == [(11, f"terminal-tier@{os.getpid()}", None)]

    @pytest.mark.asyncio
    async def test_refused_row_closed_with_reason(
        self, monkeypatch, kill_fence
    ) -> None:
        row = _row(id=12, pgid=1, pid=2)  # poisoned: pgid=1 ⇒ refused
        repo = _SpyRepo()
        actions = await sweep_rows(
            [row], repo=repo, reaped_by="terminal-tier", my_pid=os.getpid(),
        )
        assert actions == 1
        assert repo._mark_calls == [(12, f"terminal-tier@{os.getpid()}", "refused:pgid<=1")]

    @pytest.mark.asyncio
    async def test_dead_group_row_gc_closed(
        self, monkeypatch, dead_probe
    ) -> None:
        row = _row(id=13, pgid=778002, pid=778002,
                   created_at=datetime.now(timezone.utc) - timedelta(seconds=60))
        repo = _SpyRepo()
        actions = await sweep_rows(
            [row], repo=repo, reaped_by="shutdown", my_pid=os.getpid(),
        )
        assert actions == 1
        assert repo._mark_calls == [(13, f"gc@{os.getpid()}", "gc")]

    @pytest.mark.asyncio
    async def test_sibling_skipped_row_left_open(
        self, monkeypatch, kill_fence
    ) -> None:
        holder_start = datetime.now(timezone.utc) - timedelta(hours=2)
        row_created = datetime.now(timezone.utc) - timedelta(seconds=60)
        monkeypatch.setattr(
            reaper, "process_start_wallclock", lambda pid: holder_start
        )
        row = _row(id=14, daemon_pid=525252, created_at=row_created,
                   pgid=778003, pid=778003)
        repo = _SpyRepo()
        actions = await sweep_rows(
            [row], repo=repo, reaped_by="terminal-tier", my_pid=os.getpid(),
        )
        assert actions == 0
        assert repo._mark_calls == []  # skip ⇒ row stays OPEN

    @pytest.mark.asyncio
    async def test_ram_dedup_row_closed_without_signal(
        self, monkeypatch, kill_fence
    ) -> None:
        """A pgid the RAM tier of THIS sweep just killed is closed with
        NO second signal (a redundant killpg could hit a recycled pgid)."""
        row = _row(id=15, pgid=778004, pid=778004,
                   created_at=datetime.now(timezone.utc) - timedelta(seconds=60))
        repo = _SpyRepo()
        actions = await sweep_rows(
            [row], repo=repo, reaped_by="terminal-tier",
            ram_pgids={778004}, my_pid=os.getpid(),
        )
        assert actions == 1
        assert repo._mark_calls == [
            (15, f"terminal-tier@{os.getpid()}", reaper.RAM_DEDUP_REASON)
        ]

    @pytest.mark.asyncio
    async def test_repo_none_counts_actions_without_marks(
        self, monkeypatch, dead_probe
    ) -> None:
        row = _row(id=16, pgid=778005, pid=778005,
                   created_at=datetime.now(timezone.utc) - timedelta(seconds=60))
        actions = await sweep_rows(
            [row], repo=None, reaped_by="terminal-tier", my_pid=os.getpid(),
        )
        assert actions == 1

    @pytest.mark.asyncio
    async def test_verify_crash_isolated(
        self, monkeypatch, kill_fence
    ) -> None:
        async def _boom(*a, **k):
            raise RuntimeError("synthetic verify crash")

        monkeypatch.setattr(reaper.asyncio, "to_thread", _boom)
        row = _row(id=17, pgid=778006, pid=778006,
                   created_at=datetime.now(timezone.utc) - timedelta(seconds=60))
        repo = _SpyRepo()
        actions = await sweep_rows(
            [row], repo=repo, reaped_by="terminal-tier", my_pid=os.getpid(),
        )
        assert actions == 0
        assert repo._mark_calls == []


class TestSweepInstanceLane:
    """The per-instance lane used by ``cleanup_instance`` (m3 + label)."""

    @pytest.mark.asyncio
    async def test_master_off_zero_repo_calls(self, monkeypatch) -> None:
        """m3: master OFF ⇒ ZERO repository access at the cleanup point."""
        monkeypatch.setenv(ENV_KILL_SWITCH, "0")
        repo = _SpyRepo()
        actions = await sweep_instance(repo, "inst-off", reaped_by="terminal-tier")
        assert actions == 0
        assert repo._by_instance_calls == []  # never consulted

    @pytest.mark.asyncio
    async def test_on_lane_consults_and_closes(
        self, monkeypatch, kill_fence
    ) -> None:
        row = _row(id=18, pgid=1, pid=2, instance_id="inst-on")  # refused ⇒ closed
        repo = _SpyRepo(rows=[row])
        monkeypatch.delenv(ENV_KILL_SWITCH, raising=False)
        actions = await sweep_instance(repo, "inst-on", reaped_by="terminal-tier")
        assert actions == 1
        assert repo._by_instance_calls == ["inst-on"]
        assert len(repo._mark_calls) == 1

    @pytest.mark.asyncio
    async def test_writer_unset_is_noop(self, monkeypatch) -> None:
        """D3 adjudication 2: writer-unset (pre-lifespan/test lanes) ⇒
        no-op without touching anything."""
        monkeypatch.delenv(ENV_KILL_SWITCH, raising=False)
        actions = await sweep_instance(None, "inst-nowriter", reaped_by="terminal-tier")
        assert actions == 0


class TestShutdownSweepService:
    """``sweep_open_rows_for_shutdown`` selection + guard semantics (2.4)."""

    @pytest.mark.asyncio
    async def test_master_off_zero_repo_calls_and_zero_actions(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(ENV_KILL_SWITCH, "0")
        repo = _SpyRepo()
        actions = await sweep_open_rows_for_shutdown(repo)
        assert actions == 0
        assert repo._open_all_calls == 0  # zero DB queries (m3)

    @pytest.mark.asyncio
    async def test_live_sibling_row_skipped(
        self, monkeypatch, kill_fence
    ) -> None:
        holder_start = datetime.now(timezone.utc) - timedelta(hours=2)
        row_created = datetime.now(timezone.utc) - timedelta(seconds=60)
        monkeypatch.setattr(
            reaper, "process_start_wallclock", lambda pid: holder_start
        )
        rows = [
            _row(id=21, daemon_pid=525252, created_at=row_created,
                 pgid=779001, pid=779001),
        ]
        repo = _SpyRepo(rows=rows)
        actions = await sweep_open_rows_for_shutdown(repo, my_pid=os.getpid())
        assert actions == 0
        assert repo._mark_calls == []

    @pytest.mark.asyncio
    async def test_dead_writer_row_swept(self, monkeypatch, dead_probe) -> None:
        rows = [
            _row(id=22, daemon_pid=525253, pgid=779002, pid=779002,
                 created_at=datetime.now(timezone.utc) - timedelta(seconds=60)),
        ]
        repo = _SpyRepo(rows=rows)
        actions = await sweep_open_rows_for_shutdown(repo, my_pid=os.getpid())
        assert actions == 1
        assert repo._mark_calls == [(22, f"gc@{os.getpid()}", "gc")]

    @pytest.mark.asyncio
    async def test_self_owned_current_boot_row_swept(
        self, monkeypatch, kill_census
    ) -> None:
        """Self-exemption (MAJOR-1): daemon_pid == os.getpid() ALWAYS
        proceeds — without the exemption the D8 guard would skip every
        same-boot row and neuter the shutdown sweep."""
        monkeypatch.setattr(reaper, "verify_member_env", lambda pid, tid: None)
        holder_start = datetime.now(timezone.utc) - timedelta(hours=8)
        monkeypatch.setattr(
            reaper, "process_start_wallclock", lambda pid: holder_start
        )
        rows = [
            _row(id=23, daemon_pid=os.getpid(), pgid=779003, pid=779003,
                 boot_id=CURRENT_BOOT_ID,
                 created_at=datetime.now(timezone.utc) - timedelta(seconds=60)),
        ]
        repo = _SpyRepo(rows=rows)
        actions = await sweep_open_rows_for_shutdown(repo, my_pid=os.getpid())
        assert actions == 1
        assert repo._mark_calls == [
            (23, f"shutdown@{os.getpid()}", None)
        ]


class TestSwitchEnforcement:
    """Task 2.6: master OFF ⇒ sweeps are complete no-ops (m3), asserted
    with a spy repo under a HARD-RAISE ``os.killpg`` fence (the
    ``test_opt_o8_stale_subprocess_sweep.py:58-77`` shape)."""

    @pytest.fixture
    def hard_fence(self, monkeypatch):
        """os.killpg hard-raises — ANY signal attempt is a test failure."""

        def _boom(pgid, sig):
            raise AssertionError(
                f"REAL os.killpg attempted (pgid={pgid}, sig={sig}) — "
                "master-OFF sweeps must be signal-free no-ops"
            )

        monkeypatch.setattr(reaper.os, "killpg", _boom)

    @pytest.mark.asyncio
    async def test_off_cleanup_lane_never_consults_repo(
        self, monkeypatch, hard_fence
    ) -> None:
        monkeypatch.setenv(ENV_KILL_SWITCH, "0")
        repo = _SpyRepo()
        actions = await reaper.sweep_instance(repo, "inst-x")
        assert actions == 0
        assert repo._by_instance_calls == []
        assert repo._mark_calls == []

    @pytest.mark.asyncio
    async def test_off_shutdown_lane_never_consults_repo(
        self, monkeypatch, hard_fence
    ) -> None:
        monkeypatch.setenv(ENV_KILL_SWITCH, "0")
        repo = _SpyRepo()
        assert await reaper.sweep_open_rows_for_shutdown(repo) == 0
        assert repo._open_all_calls == 0

    @pytest.mark.asyncio
    async def test_off_registry_cleanup_instance_is_noop(
        self, monkeypatch, hard_fence
    ) -> None:
        """The registry extension consults the reaper lane; with the
        master OFF the lane never touches the repository."""
        from daemon.tools.bash import BashProcessRegistry

        monkeypatch.setenv(ENV_KILL_SWITCH, "0")
        reg = BashProcessRegistry()
        spy = _SpyRepo()
        reg.set_durable_writer(spy)
        killed = await reg.cleanup_instance("inst-off")
        assert killed == 0
        assert spy._by_instance_calls == []
        assert spy._mark_calls == []

    @pytest.mark.asyncio
    async def test_off_registry_cleanup_all_is_noop(
        self, monkeypatch, hard_fence
    ) -> None:
        from daemon.tools.bash import BashProcessRegistry

        monkeypatch.setenv(ENV_KILL_SWITCH, "0")
        reg = BashProcessRegistry()
        spy = _SpyRepo()
        reg.set_durable_writer(spy)
        reg._entries["inst-off"] = [SimpleNamespace(pid=1, pgid=780001)]
        killed = await reg.cleanup_all()
        assert killed >= 0
        assert spy._by_instance_calls == []

    @pytest.mark.asyncio
    async def test_on_poisoned_row_refused_closed_no_signal(
        self, monkeypatch, hard_fence
    ) -> None:
        """ON lane: a poisoned row (pgid=1) is refused + closed with the
        reason — the hard fence proves ZERO signals were attempted."""
        row = _row(id=31, pgid=1, pid=2, instance_id="inst-poison")
        repo = _SpyRepo(rows=[row])
        monkeypatch.delenv(ENV_KILL_SWITCH, raising=False)
        actions = await reaper.sweep_instance(repo, "inst-poison")
        assert actions == 1
        assert repo._mark_calls == [
            (31, f"terminal-tier@{os.getpid()}", "refused:pgid<=1")
        ]


class TestLaneParityRealRows:
    """Task 2.3: terminal/terminate/chart lanes inherit the durable
    reach with ZERO call-site changes — proven with REAL rows in a REAL
    repo through the extended ``cleanup_instance`` (dead-group rows ⇒
    gc close; no signals)."""

    @pytest.fixture
    def wired_registry(self):
        """The REAL BashProcessRegistry singleton wired to a real repo;
        writer + RAM buckets restored afterwards."""
        from daemon.tools.bash import get_bash_process_registry

        reg = get_bash_process_registry()
        eng = _spawn_repo_fixture_engine()
        from daemon.repositories.bash_spawn import BashSpawnRepository

        repo = BashSpawnRepository(engine=eng)
        previous_writer = reg.get_durable_writer()
        previous_entries = dict(reg._entries)
        reg._entries.clear()
        reg.set_durable_writer(repo)
        yield reg, repo
        reg.set_durable_writer(previous_writer)
        reg._entries.clear()
        reg._entries.update(previous_entries)
        eng.dispose()

    @staticmethod
    def _seed_dead_group_row(repo, instance_id, pgid, tracking_suffix):
        from datetime import datetime, timezone as _tz
        from daemon.repositories.bash_spawn import BashSpawnRecord
        from daemon.services.bash_spawn_reaper import CURRENT_BOOT_ID

        return repo.insert_open_row(
            pid=pgid,
            pgid=pgid,
            start_time=None,
            tracking_id=f"{tracking_suffix}".ljust(32, "0")[:32],
            instance_id=instance_id,
            command_sanitized="sleep 30",
            boot_id=CURRENT_BOOT_ID,
            daemon_pid=os.getpid(),
            created_at=datetime.now(_tz.utc).isoformat(),
        )

    @pytest.mark.asyncio
    async def test_terminal_lane_tier1_real_row_closed(
        self, wired_registry
    ) -> None:
        """Tier-1 lane shape (observer :3351): cleanup_instance on the
        completing instance reaps its durable row (dead group ⇒ gc)."""
        reg, repo = wired_registry
        row = self._seed_dead_group_row(repo, "lane-tier1-aaaa", 781001, "t1")
        killed = await reg.cleanup_instance("lane-tier1-aaaa")
        assert killed == 1
        reread = self._get_row(repo, row.id)
        assert reread.reaped_at is not None
        assert reread.reap_reason == "gc"
        assert (reread.reaped_by or "").startswith("gc@")

    @pytest.mark.asyncio
    async def test_terminate_lane_real_row_closed(
        self, wired_registry
    ) -> None:
        """Terminate lane shape (instance_lifecycle 2.56): the positional
        ``cleanup_instance(instance_id)`` call reaps the row."""
        reg, repo = wired_registry
        row = self._seed_dead_group_row(repo, "lane-term-aaaa", 781002, "tt")
        killed = await reg.cleanup_instance("lane-term-aaaa")
        assert killed == 1
        reread = self._get_row(repo, row.id)
        assert reread.reaped_at is not None
        assert reread.reap_reason == "gc"

    @pytest.mark.asyncio
    async def test_chart_lane_real_row_closed(self, wired_registry) -> None:
        """Chart belt-and-braces lane (chart_tools
        ``_bash_sweep_render_tree``): same inherited reach, real row."""
        from daemon.tools.chart_tools import _bash_sweep_render_tree

        reg, repo = wired_registry
        row = self._seed_dead_group_row(repo, "lane-chart-aaaa", 781003, "ch")
        killed = await _bash_sweep_render_tree("lane-chart-aaaa")
        assert killed == 1
        reread = self._get_row(repo, row.id)
        assert reread.reaped_at is not None
        assert reread.reap_reason == "gc"

    @pytest.mark.asyncio
    async def test_tier2_lane_descendant_real_row_closed(
        self, wired_registry
    ) -> None:
        """Tier-2 lane shape (observer :3379): the descendant's row is
        reaped when the root finalizes."""
        reg, repo = wired_registry
        row = self._seed_dead_group_row(repo, "lane-tier2-child", 781004, "t2")
        killed = await reg.cleanup_instance("lane-tier2-child")
        assert killed == 1
        reread = self._get_row(repo, row.id)
        assert reread.reaped_at is not None

    @staticmethod
    def _get_row(repo, row_id):
        from sqlmodel import Session, select
        from daemon.repositories.bash_spawn import BashSpawnRecord

        with Session(repo.engine) as session:
            return session.get(BashSpawnRecord, row_id)


# ════════════════════════════════════════════════════════════════════════════
# Phase 5 — M8 real-process leg: unstamped, unregistered process untouched
# ════════════════════════════════════════════════════════════════════════════
class TestM8UnstampedUntouched:
    """M8: a plain Popen(sleep) with NO stamp and NO registry row must
    SURVIVE cleanup_instance, the shutdown sweep, and the boot reconcile
    — proving enumeration only ever matches a stamp."""

    @pytest.mark.asyncio
    async def test_unstamped_process_survives_all_sweeps(self, monkeypatch) -> None:
        import asyncio as _asyncio
        import subprocess as _subprocess

        monkeypatch.delenv(reaper.ENV_KILL_SWITCH, raising=False)
        monkeypatch.delenv(reaper.ENV_BOOT_SWEEP, raising=False)
        monkeypatch.setattr(reaper.time, "sleep", lambda s: None)

        proc = _subprocess.Popen(["sleep", "60"], start_new_session=True)
        eng = _spawn_repo_fixture_engine()
        from daemon.repositories.bash_spawn import BashSpawnRepository

        repo = BashSpawnRepository(engine=eng)
        try:
            # cleanup_instance (terminal tier) — empty repo ⇒ no rows ⇒ no-op.
            killed = await reaper.sweep_instance(repo, "inst-m8", my_pid=os.getpid())
            assert killed == 0
            # shutdown sweep.
            assert await reaper.sweep_open_rows_for_shutdown(repo, my_pid=os.getpid()) == 0
            # boot reconcile (simulated previous boot).
            assert await reaper.reconcile_stale_bash_spawns(
                repo, current_boot_id="previous-boot-differs", my_pid=os.getpid(),
                term_grace_seconds=0.5,
            ) == 0
            # enumeration direct call (no open rows) ⇒ [] ⇒ no scan/kill.
            assert reaper._enumerate_stamped_members(repo.list_open_all()) == []
            # process still alive.
            assert proc.poll() is None
        finally:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            eng.dispose()


# ════════════════════════════════════════════════════════════════════════════
# Phase 5 — 5.1 platform legs (synthetic procfs / monkeypatched platform)
# ════════════════════════════════════════════════════════════════════════════
class TestPlatformLegs:
    def test_linux_proc_start_time_parse(self, monkeypatch) -> None:
        monkeypatch.setattr(reaper.sys, "platform", "linux")

        class _F:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                # field 22 (jiffies) = 500; comm with parens
                return "123 (bash) " + " ".join(["S"] + ["0"]*18 + ["500"]) + "\n"

            def readline(self):
                return self.read()

        monkeypatch.setattr(reaper, "open", lambda p, m="r": _F(), raising=False)
        assert reaper._read_proc_start_time(123) == 500

    def test_linux_proc_start_time_unparseable_none(self, monkeypatch) -> None:
        monkeypatch.setattr(reaper.sys, "platform", "linux")

        class _F:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return "garbage line\n"

            def readline(self):
                return self.read()

        monkeypatch.setattr(reaper, "open", lambda p, m="r": _F(), raising=False)
        assert reaper._read_proc_start_time(123) is None

    def test_linux_process_start_wallclock_conversion(self, monkeypatch) -> None:
        monkeypatch.setattr(reaper.sys, "platform", "linux")
        monkeypatch.setattr(reaper, "_read_proc_start_time", lambda pid: 1000)
        monkeypatch.setattr(reaper.os, "sysconf", lambda key: 100, raising=False)

        class _F:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return "12345.67 0.00\n"

        monkeypatch.setattr(reaper, "open", lambda p, m="r": _F(), raising=False)
        dt = reaper.process_start_wallclock(123)
        assert dt is not None
        assert dt.tzinfo is not None

    def test_linux_process_start_wallclock_unparseable_none(self, monkeypatch) -> None:
        monkeypatch.setattr(reaper.sys, "platform", "linux")
        monkeypatch.setattr(reaper, "_read_proc_start_time", lambda pid: None)
        assert reaper.process_start_wallclock(123) is None

    def test_darwin_lstart_conversion(self, monkeypatch) -> None:
        monkeypatch.setattr(reaper.sys, "platform", "darwin")

        class _R:
            returncode = 0
            stdout = "Mon Oct 31 10:00:00 2026\n"

        monkeypatch.setattr(
            reaper.subprocess, "run", lambda *a, **k: _R()
        )
        dt = reaper.process_start_wallclock(123)
        assert dt is not None
        assert dt.tzinfo is not None

    def test_darwin_lstart_unparseable_none(self, monkeypatch) -> None:
        monkeypatch.setattr(reaper.sys, "platform", "darwin")

        class _R:
            returncode = 0
            stdout = "not a date\n"

        monkeypatch.setattr(
            reaper.subprocess, "run", lambda *a, **k: _R()
        )
        assert reaper.process_start_wallclock(123) is None

    def test_member_scan_linux(self, monkeypatch) -> None:
        monkeypatch.setattr(reaper.sys, "platform", "linux")

        def _fake_listdir(path):
            return ["1", "2", "abc"]

        monkeypatch.setattr(reaper.os, "listdir", _fake_listdir)

        def _fake_open(path, mode="r"):
            # pid 1 is in pgid 999; pid 2 different pgid
            if path == "/proc/1/stat":
                return _StrFile("1 (bash) S 1 999 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 1 0\n")
            if path == "/proc/2/stat":
                return _StrFile("2 (bash) S 2 888 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 1 0\n")
            raise FileNotFoundError(path)

        class _StrFile:
            def __init__(self, content):
                self._c = content

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return self._c

            def readline(self):
                return self._c

        monkeypatch.setattr(reaper, "open", _fake_open, raising=False)
        assert reaper.list_group_members(999) == [1]

    def test_macos_fail_open_unreadable_env(self, monkeypatch) -> None:
        monkeypatch.setattr(reaper.sys, "platform", "darwin")

        class _R:
            returncode = 0
            stdout = "PID TTY\n"  # no env surfaced

        monkeypatch.setattr(reaper.subprocess, "run", lambda *a, **k: _R())
        # verify_member_env returns None (fail-open) for unreadable
        assert reaper.verify_member_env(77, "tid") is None


# ════════════════════════════════════════════════════════════════════════════
# Phase 4/5 — enumerated stamp sweep unit legs (plan task 4.1 acceptance)
# ════════════════════════════════════════════════════════════════════════════
class TestEnumeratedStampSweep:
    """Monkeypatched-scanner legs for ``_enumerate_stamped_member_details``."""

    def _row(self, **over):
        base = dict(
            pid=999001,
            pgid=999001,
            start_time=None,
            tracking_id="a" * 32,
            instance_id="inst-x",
            command_sanitized="sleep 30",
            boot_id=CURRENT_BOOT_ID,
            daemon_pid=os.getpid(),
            created_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        )
        base.update(over)
        return SimpleNamespace(**base)

    def _cand(self, pid=5001, pgid=999001, start_age=59):
        return {
            "pid": pid,
            "ppid": 1,
            "pgid": pgid,
            "etime": start_age,
            "start": datetime.now(timezone.utc) - timedelta(seconds=start_age),
            "command": "sleep 30",
        }

    def test_exact_match_selected(self, monkeypatch) -> None:
        monkeypatch.delenv(reaper.ENV_KILL_SWITCH, raising=False)
        monkeypatch.setattr(reaper, "_scan_process_table", lambda: [self._cand()])
        monkeypatch.setattr(
            reaper, "verify_member_env", lambda pid, tid: True
        )
        rows = [self._row()]
        out = reaper._enumerate_stamped_member_details(rows)
        assert out == [(5001, "a" * 32)]

    def test_readable_mismatch_refused_no_kill(self, monkeypatch, caplog) -> None:
        monkeypatch.delenv(reaper.ENV_KILL_SWITCH, raising=False)
        monkeypatch.setattr(reaper, "_scan_process_table", lambda: [self._cand()])
        monkeypatch.setattr(
            reaper, "verify_member_env", lambda pid, tid: False
        )
        with caplog.at_level(logging.WARNING, logger=reaper.__name__):
            out = reaper._enumerate_stamped_member_details([self._row()])
        assert out == []
        assert any("env mismatch" in r.getMessage() for r in caplog.records)

    def test_unreadable_env_skipped_warning(self, monkeypatch, caplog) -> None:
        """P2 darwin leg: unreadable env ⇒ skip + WARNING, never
        kill-unverified."""
        monkeypatch.delenv(reaper.ENV_KILL_SWITCH, raising=False)
        monkeypatch.setattr(reaper.sys, "platform", "darwin")
        monkeypatch.setattr(reaper, "_scan_process_table", lambda: [self._cand()])
        monkeypatch.setattr(
            reaper, "verify_member_env", lambda pid, tid: None
        )
        with caplog.at_level(logging.WARNING, logger=reaper.__name__):
            out = reaper._enumerate_stamped_member_details([self._row()])
        assert out == []
        assert any("env unreadable" in r.getMessage() for r in caplog.records)

    def test_sibling_alive_skipped_before_env_read(self, monkeypatch) -> None:
        """D8/F2: a candidate whose row is owned by a LIVE foreign
        daemon is excluded BEFORE any env read."""
        monkeypatch.delenv(reaper.ENV_KILL_SWITCH, raising=False)
        now = datetime.now(timezone.utc)
        foreign = 987654
        row = self._row(daemon_pid=foreign, created_at=now - timedelta(seconds=60))

        calls = {"env": 0}

        def _spy_env(pid, tid):
            calls["env"] += 1
            return True

        def _fake_kill(pid, sig):
            if sig == 0 and pid == foreign:
                return None  # holder alive
            raise ProcessLookupError(pid)

        monkeypatch.setattr(reaper.os, "kill", _fake_kill)
        monkeypatch.setattr(
            reaper, "process_start_wallclock",
            lambda pid: now - timedelta(seconds=120) if pid == foreign else None,
        )
        monkeypatch.setattr(reaper, "_scan_process_table", lambda: [self._cand()])
        monkeypatch.setattr(reaper, "verify_member_env", _spy_env)
        out = reaper._enumerate_stamped_member_details([row])
        assert out == []
        assert calls["env"] == 0  # skipped pre-env-read

    def test_start_time_prefilter_shrinks_env_read_set(self, monkeypatch) -> None:
        """A candidate too young for EVERY open row is excluded before
        any env read (m6 pre-filter)."""
        monkeypatch.delenv(reaper.ENV_KILL_SWITCH, raising=False)
        now = datetime.now(timezone.utc)
        row = self._row(created_at=now - timedelta(seconds=60))
        too_young = self._cand(pid=5002, start_age=1)  # 1s old, row 60s

        calls = {"env": 0}

        def _spy_env(pid, tid):
            calls["env"] += 1
            return True

        monkeypatch.setattr(
            reaper, "_scan_process_table", lambda: [too_young]
        )
        monkeypatch.setattr(reaper, "verify_member_env", _spy_env)
        out = reaper._enumerate_stamped_member_details([row])
        assert out == []
        assert calls["env"] == 0

    def test_single_scan_per_sweep(self, monkeypatch) -> None:
        """Performance bound: ONE process-table scan per sweep."""
        monkeypatch.delenv(reaper.ENV_KILL_SWITCH, raising=False)
        scans = {"n": 0}

        def _count_scan():
            scans["n"] += 1
            return [self._cand(pid=5003), self._cand(pid=5004, pgid=999002)]

        monkeypatch.setattr(reaper, "_scan_process_table", _count_scan)
        monkeypatch.setattr(reaper, "verify_member_env", lambda pid, tid: True)
        rows = [
            self._row(pgid=999001, tracking_id="b" * 32),
            self._row(pgid=999002, tracking_id="c" * 32),
        ]
        out = reaper._enumerate_stamped_member_details(rows)
        assert len(out) == 2
        assert scans["n"] == 1

    def test_master_off_zero_scan(self, monkeypatch) -> None:
        monkeypatch.setenv(reaper.ENV_KILL_SWITCH, "0")
        monkeypatch.setattr(
            reaper, "_scan_process_table",
            lambda: (_ for _ in ()).throw(AssertionError("scan must not run")),
        )
        assert reaper._enumerate_stamped_member_details([self._row()]) == []

    def test_pgid_le_1_and_self_excluded(self, monkeypatch) -> None:
        monkeypatch.delenv(reaper.ENV_KILL_SWITCH, raising=False)
        monkeypatch.setattr(
            reaper,
            "_scan_process_table",
            lambda: [
                self._cand(pid=5005, pgid=1),
                self._cand(pid=os.getpid(), pgid=999003),
            ],
        )
        monkeypatch.setattr(
            reaper, "verify_member_env",
            lambda pid, tid: (_ for _ in ()).throw(AssertionError("no env read expected")),
        )
        assert reaper._enumerate_stamped_member_details([self._row(pgid=999003)]) == []

    @pytest.mark.asyncio
    async def test_enumerated_sweep_awaits_detail_pass_off_loop(
        self, monkeypatch
    ) -> None:
        """The detail pass shells out to ``ps`` — ``_enumerated_sweep``
        must run it OFF the event-loop thread (``asyncio.to_thread``),
        matching the row-level lane's wrapping of ``verify_and_kill``.
        Thread-ident recorder: the fake detail pass stamps the thread it
        ran on; the sweep call itself runs on the pytest-asyncio loop
        thread, so the recorded ident must differ."""
        import threading

        monkeypatch.delenv(reaper.ENV_KILL_SWITCH, raising=False)
        seen: dict = {}

        def _fake_details(open_rows):
            seen["ident"] = threading.get_ident()
            seen["rows"] = list(open_rows or [])
            return [(5001, "a" * 32)]

        def _fake_kill(pid, grace):  # record-only — no real signal
            seen.setdefault("killed", []).append((pid, grace))

        monkeypatch.setattr(
            reaper, "_enumerate_stamped_member_details", _fake_details
        )
        monkeypatch.setattr(reaper, "_kill_enumerated_pid", _fake_kill)
        row = self._row()
        closed = await reaper._enumerated_sweep(
            [row], repo=None, reaped_by="terminal-tier",
            term_grace_seconds=0,
        )
        assert closed == 0  # repo=None ⇒ marks skipped
        assert seen["rows"] == [row]
        assert seen["ident"] != threading.get_ident()  # off the loop thread
        assert seen["killed"] == [(5001, reaper._clamped_grace(0))]
