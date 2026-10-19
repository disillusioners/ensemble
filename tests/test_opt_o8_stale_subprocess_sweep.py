"""Tests for the boot-time stale-subprocess sweep (resource-cost-control O8a).

FENCING (project critical note, 2026-10-12 incident class — zero tolerance):
every /proc surface here is a SYNTHETIC tree built under pytest ``tmp_path``
(the mktemp-equivalent); the autouse ``_fence_no_real_signals`` fixture
monkeypatches ``os.kill`` to a hard-raising stub so even a bug that bypasses
signal injection can never deliver a real signal, and ``time.sleep`` is
stubbed so no test ever really waits. No test touches HOME-derived or
live-shaped paths; process "death" is simulated by removing directories
inside tmp_path only.

Pins (plan D13/T9/AC10):
* stale child reaped TERM → grace → KILL, in order;
* prior-incarnation candidate detection (older starttime, same cgroup);
* current-tree and ancestor exclusion (the launcher/bootloader save);
* PID-recycle guard — starttime mismatch (any phase) → skip, never kill;
* kill-switch OFF = complete no-op (zero reads — proven against a
  nonexistent proc root); default-ON pinned;
* unknown/missing/corrupt /proc entries handled without raising;
* sibling-unit cgroup members (ensemble-demo) structurally excluded;
* kernel threads / zombies / pid 1 never targeted.
"""

from __future__ import annotations

import logging
import os
import signal
from pathlib import Path

import pytest

from daemon.services import boot_subprocess_sweep as sweep_mod
from daemon.services.boot_subprocess_sweep import (
    DEFAULT_ENABLED,
    ENV_KILL_SWITCH,
    ENV_TERM_GRACE_SECONDS,
    is_prod_lane,
    kill_switch_from_env,
    sweep_stale_subprocesses,
    term_grace_seconds_from_env,
)

UNIT_CGROUP = "/system.slice/ensemble-main.service"
DEMO_CGROUP = "/system.slice/ensemble-demo.service"

# Clock ticks for fake starttimes. Daemon starts at 10_000; anything
# strictly older is a prior-incarnation candidate IF it sits in the unit
# cgroup and outside the daemon's lineage.
T_LAUNCHER = 9_000
T_BOOTLOADER = 9_950
T_DAEMON = 10_000
T_STALE = 5_000


# ── Fences (autouse) ───────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fence_no_real_signals(monkeypatch):
    """HARD fence: os.kill raises in tests. A real signal is unreachable."""

    def _boom(pid, sig):
        raise AssertionError(
            f"REAL os.kill attempted in tests (pid={pid}, sig={sig}) — "
            "tests must only signal via the injected recorder"
        )

    monkeypatch.setattr(os, "kill", _boom)


@pytest.fixture(autouse=True)
def _fence_no_real_sleep(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(
        sweep_mod.time, "sleep", lambda s: sleeps.append(s), raising=True
    )
    yield sleeps


@pytest.fixture(autouse=True)
def _fence_clean_env(monkeypatch):
    """Ambient env on a dev box must never flip sweep semantics."""
    monkeypatch.delenv(ENV_KILL_SWITCH, raising=False)
    monkeypatch.delenv(ENV_TERM_GRACE_SECONDS, raising=False)


# ── Synthetic /proc builder ────────────────────────────────────────────────

class FakeProc:
    """A synthetic /proc-style tree under tmp_path (FAKE fence)."""

    def __init__(self, root: Path):
        assert str(root).startswith(str(root.anchor))  # absolute
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        # /proc/stat with btime (age reporting only)
        (self.root / "stat").write_text(
            "cpu  0 0 0 0 0 0 0 0 0 0\nbtime 1700000000\n"
        )

    def _stat_text(self, pid, comm, state, ppid, pgrp, sess, starttime):
        # fields after the paren: state(0) ppid(1) pgrp(2) sess(3)
        # then 15 fillers (idx 4..18), starttime(19).
        return (
            f"{pid} ({comm}) {state} {ppid} {pgrp} {sess}"
            + " 0" * 15
            + f" {starttime}"
            + " 0" * 10
        )

    def add(
        self,
        pid: int,
        *,
        comm: str = "proc",
        ppid: int = 1,
        pgrp: int | None = None,
        sess: int | None = None,
        starttime: int = T_STALE,
        cmdline: str = "sleep 30",
        cgroup: str = UNIT_CGROUP,
        state: str = "S",
    ) -> int:
        d = self.root / str(pid)
        d.mkdir(exist_ok=True)
        pgrp = pid if pgrp is None else pgrp
        sess = pid if sess is None else sess
        (d / "stat").write_text(
            self._stat_text(pid, comm, state, ppid, pgrp, sess, starttime)
        )
        (d / "cmdline").write_text(cmdline)
        (d / "cgroup").write_text(f"0::{cgroup}\n")
        return pid

    def set_starttime(self, pid: int, starttime: int) -> None:
        """Simulate a PID RECYCLE: same pid, fresh process identity."""
        d = self.root / str(pid)
        text = (d / "stat").read_text()
        parts = text.rsplit(")", 1)
        fields = parts[1].split()
        fields[19] = str(starttime)
        (d / "stat").write_text(parts[0] + ")" + " ".join(fields))

    def kill_dir(self, pid: int) -> None:
        """Simulate process death: the /proc entry disappears."""
        d = self.root / str(pid)
        if d.exists():
            for f in d.iterdir():
                f.unlink()
            d.rmdir()


def standard_tree(fp: FakeProc) -> dict:
    """The census-shaped tree: launcher → bootloader → daemon + a stale orphan.

    launcher(300) and bootloader(310) start EARLIER than daemon(400) inside
    the same cgroup — the ancestry exclusion must save them (live census:
    launcher −6s, bootloader −1s). pid 500 is a prior-incarnation MCP-class
    orphan (setsid'd: own pgrp/session, reparented to pid 1).
    """
    fp.add(300, comm="launcher.sh", ppid=1, starttime=T_LAUNCHER,
           cmdline="/bin/bash /home/nea/agents-ensemble/launcher.sh")
    fp.add(310, comm="ensemble-prod", ppid=300, starttime=T_BOOTLOADER,
           cmdline="/home/nea/agents-ensemble/current/ensemble-prod")
    fp.add(400, comm="ensemble-prod", ppid=310, starttime=T_DAEMON,
           cmdline="/home/nea/agents-ensemble/current/ensemble-prod")
    fp.add(500, comm="node", ppid=1, starttime=T_STALE,
           cmdline="node /home/nea/.npm/_npx/x/node_modules/.bin/context7-mcp")
    return {"launcher": 300, "bootloader": 310, "daemon": 400, "orphan": 500}


class SignalRecorder:
    """Injected signal_fn: records calls; simulates death on chosen sigs."""

    def __init__(self, fp: FakeProc, die_on=("SIGKILL",)):
        self.fp = fp
        self.die_on = set(die_on)
        self.calls: list[tuple[int, str]] = []

    def __call__(self, pid: int, sig: signal.Signals) -> None:
        self.calls.append((pid, sig.name))
        if sig.name in self.die_on:
            self.fp.kill_dir(pid)


@pytest.fixture
def env(tmp_path: Path) -> FakeProc:
    return FakeProc(tmp_path / "proc")


# ── Pins ───────────────────────────────────────────────────────────────────

class TestReapOrdering:
    def test_stale_child_reaped_term_then_grace_then_kill(self, env):
        """TERM → (single bounded grace sleep) → KILL, per candidate, in order."""
        ids = standard_tree(env)
        rec = SignalRecorder(env, die_on=("SIGKILL",))  # survives TERM

        report = sweep_stale_subprocesses(
            proc_root=str(env.root),
            daemon_pid=ids["daemon"],
            kill_switch=True,
            grace_seconds=2.0,
            signal_fn=rec,
            sleep_fn=lambda s: None,
        )

        assert rec.calls == [
            (500, "SIGTERM"),
            (500, "SIGKILL"),
        ], f"unexpected signal order: {rec.calls}"
        phases = [(a.pid, a.phase, a.result) for a in report.actions]
        assert phases == [(500, "term", "signaled"), (500, "kill", "signaled")]
        assert report.scanned == 4

    def test_child_exiting_on_term_never_gets_kill(self, env):
        ids = standard_tree(env)
        rec = SignalRecorder(env, die_on=("SIGTERM",))  # TERM is enough

        report = sweep_stale_subprocesses(
            proc_root=str(env.root),
            daemon_pid=ids["daemon"],
            kill_switch=True,
            grace_seconds=1.0,
            signal_fn=rec,
            sleep_fn=lambda s: None,
        )

        assert rec.calls == [(500, "SIGTERM")]
        assert not [
            a for a in report.actions
            if a.phase == "kill" and a.result == "signaled"
        ]

    def test_grace_window_is_bounded_and_observed(self, env):
        ids = standard_tree(env)
        sleeps: list[float] = []
        sweep_stale_subprocesses(
            proc_root=str(env.root),
            daemon_pid=ids["daemon"],
            kill_switch=True,
            grace_seconds=999.0,  # argument path is clamped too
            signal_fn=lambda pid, sig: None,
            sleep_fn=sleeps.append,
        )
        assert sleeps == [30.0]  # MAX_TERM_GRACE_SECONDS


class TestExclusions:
    def test_current_tree_never_signaled_even_with_older_starttime(self, env):
        """A forged older-starttime CHILD of the daemon is still protected.

        (Real tree children are always younger; the exclusion is structural,
        not arithmetic — pinned so a clock/tick confusion can never turn it
        into a reap of our own subtree.)
        """
        ids = standard_tree(env)
        env.add(450, comm="code-server", ppid=ids["daemon"], starttime=T_STALE,
                cmdline="/usr/local/lib/code-server/lib/node code-server")
        rec = SignalRecorder(env)

        sweep_stale_subprocesses(
            proc_root=str(env.root),
            daemon_pid=ids["daemon"],
            kill_switch=True,
            signal_fn=rec,
            sleep_fn=lambda s: None,
        )
        assert 450 not in [p for p, _ in rec.calls]
        assert {p for p, _ in rec.calls} == {500}

    def test_ancestors_launcher_and_bootloader_never_signaled(self, env):
        """THE census-critical exclusion: the launcher + PyInstaller
        bootloader predate the daemon inside the same cgroup."""
        ids = standard_tree(env)
        rec = SignalRecorder(env)

        sweep_stale_subprocesses(
            proc_root=str(env.root),
            daemon_pid=ids["daemon"],
            kill_switch=True,
            signal_fn=rec,
            sleep_fn=lambda s: None,
        )
        # default recorder dies at SIGKILL → both signals recorded; the
        # load-bearing assertion is that ONLY the orphan was ever a target:
        assert {p for p, _ in rec.calls} == {500}

    def test_sibling_unit_cgroup_never_touched(self, env):
        """ensemble-demo.service members are structurally out of scope."""
        ids = standard_tree(env)
        env.add(600, comm="node", ppid=1, starttime=T_STALE,
                cmdline="node .bin/context7-mcp", cgroup=DEMO_CGROUP)
        rec = SignalRecorder(env)

        report = sweep_stale_subprocesses(
            proc_root=str(env.root),
            daemon_pid=ids["daemon"],
            kill_switch=True,
            signal_fn=rec,
            sleep_fn=lambda s: None,
        )
        assert 600 not in [p for p, _ in rec.calls]
        assert [c.pid for c in report.candidates] == [500]

    def test_init_kernel_threads_and_zombies_excluded(self, env):
        ids = standard_tree(env)
        env.add(1, comm="systemd", ppid=0, starttime=0, cmdline="/sbin/init",
                cgroup=UNIT_CGROUP)  # even in-cgroup: pid<=1 is off-limits
        env.add(60, comm="kworker/0:1", ppid=2, starttime=T_STALE,
                cmdline="", cgroup=UNIT_CGROUP)  # kernel thread: no cmdline
        env.add(700, comm="python", ppid=1, starttime=T_STALE, state="Z",
                cmdline="python -m pytest", cgroup=UNIT_CGROUP)  # zombie
        rec = SignalRecorder(env)

        sweep_stale_subprocesses(
            proc_root=str(env.root),
            daemon_pid=ids["daemon"],
            kill_switch=True,
            signal_fn=rec,
            sleep_fn=lambda s: None,
        )
        assert {p for p, _ in rec.calls} == {500}

    def test_younger_or_equal_starttime_never_signaled(self, env):
        """N1 (review r2, 2026-10-18): the starttime clause
        (``sweep_stale_subprocesses.py`` ~:371) is the prior-incarnation
        oracle — a same-cgroup, NON-lineage process whose starttime is
        younger-or-equal to the daemon's own starttime is NEVER a
        candidate and NEVER signaled. Two mutations the test pins
        against:

          M1: ``>=`` → ``>`` boundary flip — pid 800 (starttime equal
              to the daemon's own) joins the candidate set and gets
              TERMed.
          M2: deletion of the clause entirely — pid 800 (boundary) AND
              pid 850 (strictly younger) BOTH join the candidate set
              and get TERMed.

        The strictly-older pid 500 (the standard-tree orphan) stays a
        legitimate target, proving the sweep is otherwise running.
        """
        ids = standard_tree(env)
        # Boundary-equal starttime — the M1 mutation would erroneously
        # admit this process when ``>=`` is flipped to ``>``. Placed in
        # the unit cgroup, not in the daemon's lineage, with a
        # legitimate user-space cmdline.
        env.add(800, comm="context7-mcp", ppid=1, starttime=T_DAEMON,
                cmdline="node /home/nea/.npm/_npx/x/node_modules/.bin/context7-mcp")
        # Strictly younger — the M2 (clause deletion) would admit this
        # process regardless of the boundary; pinned so M2 also fails
        # the test even on hosts where M1's regression never trips a
        # sibling with starttime == my_start in practice.
        env.add(850, comm="plane-mcp", ppid=1, starttime=T_DAEMON + 500,
                cmdline="node /home/nea/.npm/_npx/x/node_modules/.bin/plane-mcp")
        rec = SignalRecorder(env)

        rep = sweep_stale_subprocesses(
            proc_root=str(env.root),
            daemon_pid=ids["daemon"],
            kill_switch=True,
            signal_fn=rec,
            sleep_fn=lambda s: None,
        )

        # The legitimate target stays reaped (500 — strictly-older
        # orphan); proves the sweep is running.
        assert {p for p, _ in rec.calls} == {500}
        # The younger-or-equal processes never appeared, in either
        # the signal trace or the candidate list.
        assert 800 not in [p for p, _ in rec.calls]
        assert 850 not in [p for p, _ in rec.calls]
        candidate_pids = {c.pid for c in rep.candidates}
        assert 800 not in candidate_pids
        assert 850 not in candidate_pids


class TestPidRecycleGuard:
    def test_recycle_during_grace_skips_kill(self, env):
        """The P2 guard: pid recycled between TERM and KILL → KILL withheld."""
        ids = standard_tree(env)
        rec = SignalRecorder(env, die_on=())  # survives everything

        def fake_sleep(_s):
            # The grace window is exactly where a pid can die + recycle.
            env.kill_dir(500)
            env.add(500, comm="node", ppid=1, starttime=T_STALE + 7,
                    cmdline="node recycled-unrelated-process")

        rep = sweep_stale_subprocesses(
            proc_root=str(env.root),
            daemon_pid=ids["daemon"],
            kill_switch=True,
            grace_seconds=1.0,
            signal_fn=rec,
            sleep_fn=fake_sleep,
        )

        assert rec.calls == [(500, "SIGTERM")]  # KILL NEVER DELIVERED
        kill_actions = [a for a in rep.actions if a.phase == "kill"]
        assert kill_actions and all(
            a.result == "guard_skip"
            and a.detail.startswith("starttime_mismatch")
            for a in kill_actions
        )

    def test_recycle_before_term_signal_skips_term(self, env, monkeypatch):
        """Guard fires before EVERY signal: identity mutated between
        enumeration and the TERM-phase guard → no signal at all."""
        ids = standard_tree(env)
        rec = SignalRecorder(env)

        real_read = sweep_mod._read_proc_stat
        reads = {"n": 0}

        def counting_read(proc_root, pid):
            if pid == 500:
                reads["n"] += 1
                if reads["n"] == 2:  # 1st = enumeration, 2nd = TERM-phase guard
                    # recycled under us BEFORE the guard re-reads: the guard
                    # must observe the NEW identity, not a stale snapshot
                    env.set_starttime(500, T_STALE + 99)
            return real_read(proc_root, pid)

        monkeypatch.setattr(sweep_mod, "_read_proc_stat", counting_read)
        rep = sweep_stale_subprocesses(
            proc_root=str(env.root),
            daemon_pid=ids["daemon"],
            kill_switch=True,
            grace_seconds=1.0,
            signal_fn=rec,
            sleep_fn=lambda s: None,
        )
        assert rec.calls == []  # NEVER signaled
        assert any(
            a.pid == 500 and a.phase == "term" and a.result == "guard_skip"
            for a in rep.actions
        )

    def test_vanish_during_grace_skips_kill(self, env):
        ids = standard_tree(env)
        rec = SignalRecorder(env, die_on=("SIGTERM",))

        def fake_sleep(_s):
            env.kill_dir(500)  # died quietly after TERM, pid not yet recycled

        rep = sweep_stale_subprocesses(
            proc_root=str(env.root),
            daemon_pid=ids["daemon"],
            kill_switch=True,
            grace_seconds=1.0,
            signal_fn=rec,
            sleep_fn=fake_sleep,
        )
        assert rec.calls == [(500, "SIGTERM")]
        kill_actions = [a for a in rep.actions if a.phase == "kill"]
        assert kill_actions and all(
            a.result == "guard_skip" and a.detail == "vanished"
            for a in kill_actions
        )


class TestKillSwitchAndDefaults:
    def test_default_is_on_pinned(self):
        """Leader ruling: ships default-ON at promote.

        W1/W2 (review r2, 2026-10-18): the default is lane-conditional
        — pinned on BOTH sides AND across BOTH resolution paths:

        * prod lane (``is_prod=True``) → absent env → ON
          (the leader ruling: ``DEFAULT_ENABLED = True`` and the lane
          matches, so it applies);
        * dev/test/manual lane (``is_prod=False``) → absent env →
          OFF (the W1/W2 fix: dev-box launcher-scoped cgroup must not
          get collateral-kill breadth; the daemon's own tree is empty
          in the source-mode ``python -m daemon``/``launcher.sh``
          runs, so the sweep has no defensible target there);
        * the auto-detect path (``kill_switch_from_env({})`` with NO
          kwarg) must follow :func:`is_prod_lane` and, in pytest
          (``python -m pytest`` — a source-mode run), resolve to
          **False**. This is the assertion that catches the W1/W2
          "flip the discriminator" mutation: a regression that makes
          :func:`is_prod_lane` always return True would flip the
          auto-detect result to ON and FAIL this test.

        The literal ``DEFAULT_ENABLED = True`` (the leader ruling
        itself) is unchanged — the lane detector is what gates
        whether the default applies.
        """
        assert DEFAULT_ENABLED is True
        assert is_prod_lane() is False  # pytest is source-mode (sanity)
        # Auto-detect path (no kwarg): pytest source-mode → OFF
        assert kill_switch_from_env({}) is False
        assert kill_switch_from_env({"x": "1"}) is False
        # prod lane — leader ruling honored
        assert kill_switch_from_env({}, is_prod=True) is True
        assert kill_switch_from_env({"x": "1"}, is_prod=True) is True
        # dev/test/manual lane — W1/W2 fix honored
        assert kill_switch_from_env({}, is_prod=False) is False
        assert kill_switch_from_env({"x": "1"}, is_prod=False) is False

    def test_env_override_wins_in_dev_test(self, monkeypatch):
        """Explicit ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES wins in BOTH
        directions in BOTH lanes — the source-mode default-OFF is
        operator-overridable so a dev operator can run the sweep on a
        specific boot to verify it (no other surface exposes the
        boolean). Pinned because the sweep default-OFF is a recent
        behavior change (W1/W2) and a regression that re-anchors the
        default to ON for any env value (incl. absent) would silently
        re-enable the dev-box collateral-kill breadth that the W1/W2
        fix was written to remove.

        The ``_fence_clean_env`` autouse fixture is what guarantees
        the env is empty when this test starts — without it, an
        ambient ``ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES`` on a dev box
        could pass even after such a regression. The fixture delenvs
        the var with ``raising=False`` so the test fails LOUD if the
        fixture stops firing.
        """
        # Force-ON override in dev/test lane → ON (env wins)
        monkeypatch.setenv(ENV_KILL_SWITCH, "1")
        assert kill_switch_from_env(is_prod=False) is True
        # Force-OFF override in prod lane → OFF (env wins)
        monkeypatch.setenv(ENV_KILL_SWITCH, "0")
        assert kill_switch_from_env(is_prod=True) is False

    def test_kill_switch_disable_values(self):
        for value in ("0", "false", "no", "off", "OFF", "False"):
            assert kill_switch_from_env({ENV_KILL_SWITCH: value}) is False, value
        # anything else enables
        for value in ("1", "true", "yes", "", "  "):
            assert kill_switch_from_env({ENV_KILL_SWITCH: value}) is True, value

    def test_kill_switch_off_is_a_complete_noop(self, tmp_path):
        """OFF: no reads (nonexistent proc root proves it), no signals,
        no errors — a clean disabled report.

        W3 (review r2, 2026-10-18): the ``errors`` pin guards against
        the regression that moves the /proc enumeration BEFORE the
        kill-switch check — that mutation leaves the existing
        ``enabled/scanned/candidates/actions/calls`` assertions passing
        (the early returns still report an enabled=False empty report
        in the success case) BUT fails this test, because the
        enumeration runs against the nonexistent proc_root and
        raises FileNotFoundError, which the fail-open bucket at the
        bottom appends to ``report.errors``.
        """
        rec = SignalRecorder(FakeProc(tmp_path / "unused"))
        rep = sweep_stale_subprocesses(
            proc_root=str(tmp_path / "does-not-exist"),
            daemon_pid=12345,
            kill_switch=False,
            signal_fn=rec,
            sleep_fn=lambda s: None,
        )
        assert rep.enabled is False
        assert rep.scanned == 0
        assert rep.candidates == []
        assert rep.actions == []
        assert rep.errors == []
        assert rec.calls == []

    def test_env_kill_switch_off_via_environment(self, env, monkeypatch):
        ids = standard_tree(env)
        monkeypatch.setenv(ENV_KILL_SWITCH, "0")
        rec = SignalRecorder(env)
        rep = sweep_stale_subprocesses(
            proc_root=str(env.root),
            daemon_pid=ids["daemon"],
            signal_fn=rec,          # kill_switch resolved from env → off
            sleep_fn=lambda s: None,
        )
        assert rep.enabled is False
        assert rec.calls == []


class TestGraceKnob:
    def test_grace_env_parse_and_clamp(self):
        assert term_grace_seconds_from_env({}) == 5.0
        assert term_grace_seconds_from_env({ENV_TERM_GRACE_SECONDS: "2.5"}) == 2.5
        assert term_grace_seconds_from_env({ENV_TERM_GRACE_SECONDS: "0.5"}) == 0.5
        assert term_grace_seconds_from_env({ENV_TERM_GRACE_SECONDS: "999"}) == 30.0
        assert term_grace_seconds_from_env({ENV_TERM_GRACE_SECONDS: "0"}) == 0.5
        assert term_grace_seconds_from_env(
            {ENV_TERM_GRACE_SECONDS: "bogus"}
        ) == 5.0


class TestDegenerateProcSurfaces:
    def test_missing_and_corrupt_entries_skipped_safely(self, env):
        """Vanished pid dirs, corrupt stat, missing cmdline/cgroup files:
        skipped without raising; valid candidates still reaped."""
        ids = standard_tree(env)
        # corrupt stat: no closing paren
        bad = env.root / "501"
        bad.mkdir()
        (bad / "stat").write_text("501 (unclosed 1 2 3")
        (bad / "cmdline").write_text("whatever")
        (bad / "cgroup").write_text(f"0::{UNIT_CGROUP}\n")
        # missing cmdline file entirely
        nocmd = env.root / "502"
        nocmd.mkdir()
        (nocmd / "stat").write_text(
            env._stat_text(502, "x", "S", 1, 502, 502, T_STALE)
        )
        (nocmd / "cgroup").write_text(f"0::{UNIT_CGROUP}\n")
        rec = SignalRecorder(env, die_on=("SIGKILL",))

        rep = sweep_stale_subprocesses(
            proc_root=str(env.root),
            daemon_pid=ids["daemon"],
            kill_switch=True,
            signal_fn=rec,
            sleep_fn=lambda s: None,
        )
        assert rec.calls == [(500, "SIGTERM"), (500, "SIGKILL")]
        assert rep.errors == []

    def test_signal_processlookuperror_is_vanished_not_crash(self, env):
        ids = standard_tree(env)

        def racing_signal(pid, sig):
            env.kill_dir(pid)  # vanished before the signal landed
            raise ProcessLookupError(pid)

        rep = sweep_stale_subprocesses(
            proc_root=str(env.root),
            daemon_pid=ids["daemon"],
            kill_switch=True,
            grace_seconds=0.5,
            signal_fn=racing_signal,
            sleep_fn=lambda s: None,
        )
        assert all(
            a.result == "vanished" for a in rep.actions if a.phase == "term"
        )

    def test_unreadable_own_surface_aborts_fail_open(self, tmp_path):
        fp = FakeProc(tmp_path / "proc")
        ids = standard_tree(fp)
        # daemon 400 exists but its cgroup file is missing → abort, no signals
        (fp.root / "400" / "cgroup").unlink()
        rec = SignalRecorder(fp)
        rep = sweep_stale_subprocesses(
            proc_root=str(fp.root),
            daemon_pid=ids["daemon"],
            kill_switch=True,
            signal_fn=rec,
            sleep_fn=lambda s: None,
        )
        assert rec.calls == []
        assert rep.candidates == []
        assert rep.errors, "expected a recorded abort reason"

    def test_internal_failure_fails_open(self, env, monkeypatch):
        ids = standard_tree(env)
        monkeypatch.setattr(
            sweep_mod.os, "listdir",
            lambda p: (_ for _ in ()).throw(RuntimeError("proc exploded")),
        )
        rep = sweep_stale_subprocesses(
            proc_root=str(env.root),
            daemon_pid=ids["daemon"],
            kill_switch=True,
            signal_fn=SignalRecorder(env),
            sleep_fn=lambda s: None,
        )
        assert rep.errors and "RuntimeError" in rep.errors[-1]


class TestActionLogging:
    def test_every_action_logged_with_pid_cmdline_age(self, env, caplog):
        ids = standard_tree(env)
        with caplog.at_level(
            logging.WARNING, logger="daemon.services.boot_subprocess_sweep"
        ):
            sweep_stale_subprocesses(
                proc_root=str(env.root),
                daemon_pid=ids["daemon"],
                kill_switch=True,
                grace_seconds=0.5,
                signal_fn=SignalRecorder(env, die_on=("SIGKILL",)),
                sleep_fn=lambda s: None,
            )
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "SIGTERM stale child pid=500" in text
        assert "context7-mcp" in text          # cmdline in the log
        assert "age=" in text                   # starttime-derived age
        assert "prior-incarnation orphan" in text
        assert "SIGKILL stale child pid=500" in text
        assert "sweep complete" in text and "candidates=1" in text

    def test_no_candidates_logs_all_clear(self, env, caplog):
        """Clean box (today's live census state): zero candidates, no signals."""
        ids = standard_tree(env)
        env.kill_dir(500)  # remove the orphan → current-incarnation only
        with caplog.at_level(
            logging.INFO, logger="daemon.services.boot_subprocess_sweep"
        ):
            rep = sweep_stale_subprocesses(
                proc_root=str(env.root),
                daemon_pid=ids["daemon"],
                kill_switch=True,
                signal_fn=SignalRecorder(env),
                sleep_fn=lambda s: None,
            )
        assert rep.candidates == []
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "no candidates" in text

    def test_survivor_after_sigkill_reported_not_spun_on(self, env, caplog):
        ids = standard_tree(env)
        with caplog.at_level(
            logging.WARNING, logger="daemon.services.boot_subprocess_sweep"
        ):
            sweep_stale_subprocesses(
                proc_root=str(env.root),
                daemon_pid=ids["daemon"],
                kill_switch=True,
                grace_seconds=0.5,
                signal_fn=SignalRecorder(env, die_on=()),  # survives SIGKILL
                sleep_fn=lambda s: None,
            )
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "still present after SIGKILL" in text
