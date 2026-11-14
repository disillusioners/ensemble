"""Proc-lane RAM auto-termination tests (phase4-plan.md T11).

The pack covers the five shapes the plan names:

* (a) a REAL ballooning tree via ``proc_run`` — the tree is dead and
  ``proc_status`` carries the verdict (``ram_breach:`` + ``ram_killed: true``);
* (b) an under-limit command — the ``proc_status`` block is byte-identical
  to today for a non-RAM process (no RAM lines);
* (c) the kill-switch OFF path — no extra task, no registration;
* (d) the negative paths — a non-Linux platform guard, an unreadable
  ``/proc``, the ``MAX_PROCESSES_PER_INSTANCE = 10`` cap unaffected, the
  spawn-window orphan path unaffected;
* (e) the leaderless-tree shape (a shell that backgrounds a ballooning
  grandchild and exits).

Plus the lane contracts the plan pins: the LLM-facing ``proc_run`` schema
gains NO ceiling parameter; ``user_stopped: true`` is surfaced; the
§3.5 item-13 sentence is reproducible from the lane; no subreaper is
introduced; the switch semantics agree with the bash lane; the P6
absent-stamp ⇒ unverifiable ⇒ PROCEED + ``ownership_unverified++``
coupling; and the pending-surface peak line mirrors the bash lane's
``ram_observed_peak_bytes`` name verbatim.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import re
import shlex
import sys
import time
from pathlib import Path

import pytest
import pytest_asyncio

proc_tools_module = importlib.import_module("daemon.tools.proc_tools")
bash_module = importlib.import_module("daemon.tools.bash")
from daemon.tools.proc_tools import create_proc_tools

from daemon.services.ram_guard import set_ram_guard
from daemon.services.ram_guard.guard import RamGuard
from daemon.services.ram_guard.verdict import (
    STATE_KILLED,
    STATE_WATCHING,
    SpawnRecord,
)

INSTANCE = "inst-proc-ram-guard-tests"

import os as _os


@pytest.fixture(autouse=True)
def _ram_fence(monkeypatch, tmp_path):
    """T2 — every spawning test runs under the house fence; ASSERTED to hold."""
    from tests.helpers.ram_guard_fence import apply_fence, assert_fence_applied

    fake_home, auth_names = apply_fence(monkeypatch, tmp_path)
    yield
    assert_fence_applied(fake_home, auth_names)


@pytest.fixture
def unique_instance_id():
    return f"test-proc-{_os.urandom(4).hex()}"


@pytest_asyncio.fixture
async def proc_tools(unique_instance_id):
    from daemon.tools.proc_tools import create_proc_tools, get_background_process_manager

    tools_list = create_proc_tools(current_instance_id=unique_instance_id)
    tools = {getattr(t, "name", None): t for t in tools_list}
    yield tools, unique_instance_id
    manager = get_background_process_manager()
    await manager.cleanup_instance(unique_instance_id)


def _make_record(**overrides) -> SpawnRecord:
    """A verdict record with the substrate's real shape.

    ``samples=1`` — the guard samples the tree on its tick, so a record
    that was observed at least once is the NORMAL case (the lane's
    release path is conditional on the watch, not on the sample count
    alone).
    """
    kwargs = dict(
        tracking_id="t-recording",
        instance_id="inst-proc-ram-guard-tests",
        lane="proc",
        pid=4242,
        pgid=4242,
        start_time_jiffies=1000,
        ceiling_bytes=CEILING_BYTES,
        poll_interval_ms=100,
        grace_ms=100,
        samples=1,
    )
    kwargs.update(overrides)
    return SpawnRecord(**kwargs)


class _RecordingGuard:
    """A RECORDING fake for the guard — the ORDERING assertions.

    The fake publishes the verdict on a chosen read (``publish_after_reads``)
    so the lane's ordering — the verdict is consulted BEFORE ``info.status``
    is finalized, and the release happens AFTER the publication — is
    OBSERVABLE, not assumed (phase4-plan.md T5/T8).
    """

    def __init__(
        self,
        record: SpawnRecord | None = None,
        *,
        publish_after_reads: int | None = None,
        publish_reason: str = "killed:ladder",
    ) -> None:
        self.record = record if record is not None else _make_record()
        self.publish_after_reads = publish_after_reads
        self.publish_reason = publish_reason
        self.registered: list[dict] = []
        self.released: list[str] = []
        self.release_calls: list[str] = []
        self.events: list = []
        self._reads = 0

    # ── the guard's registry API (the facade calls exactly these) ──
    def register(self, **kwargs):
        self.events.append(("register", kwargs))
        self.registered.append(kwargs)
        if self.record is not None:
            self.record.tracking_id = kwargs.get("tracking_id", self.record.tracking_id)
            self.record.pid = kwargs.get("pid", self.record.pid)
            self.record.pgid = kwargs.get("pgid", self.record.pgid)
            self.record.start_time_jiffies = kwargs.get(
                "start_time_jiffies", self.record.start_time_jiffies
            )
            if kwargs.get("ceiling_bytes") is not None:
                self.record.ceiling_bytes = kwargs["ceiling_bytes"]
        return self.record

    def read_verdict(self, tracking_id: str):
        self._reads += 1
        self.events.append(("read_verdict", self._reads))
        if (
            self.publish_after_reads is not None
            and self._reads >= self.publish_after_reads
            and self.record is not None
            and self.record.state != STATE_KILLED
        ):
            # The execution-lane thread PUBLISHES the verdict.
            self.record.state = STATE_KILLED
            self.record.reason = self.publish_reason
            self.record.breach_at = time.time()
            self.record.killed_at = time.time()
            self.record.observed_peak_bytes = 96 * 1024 * 1024
            self.record.confirmed_pss_bytes = 96 * 1024 * 1024
            self.record.peak_is_lower_bound = True
            self.record.under_report_bound_bytes = 1024
            self.record.metric = "summed_rss"
            self.record.trigger_metric = "summed_rss"
            self.record.tree_complete = True
            self.record.members_total = 4
            self.record.members_killable = 4
            self.record.members_unreachable = 0
            self.events.append(("publication",))
        return self.record

    def release(self, tracking_id: str, *, force: bool = False) -> bool:
        self.events.append(("release", tracking_id))
        self.release_calls.append(tracking_id)
        self.released.append(tracking_id)
        return True

    def health(self) -> dict:
        return {"registry_depth": len(self.registered)}

    def counters_snapshot(self) -> dict:
        return {}


@pytest.fixture
def recording_guard():
    """Install a recording fake guard; clear it afterwards."""
    guard = _RecordingGuard()
    set_ram_guard(guard)
    try:
        yield guard
    finally:
        set_ram_guard(None)


@pytest.fixture
def real_guard():
    """A REAL guard with an INJECTED tiny ceiling (never the default).

    The guard's TWO threads (the tick thread + the execution-lane thread)
    are STARTED here — they own the measurement, the breach decision, the
    kill and the verdict (leader ruling Q1).  A guard that is installed
    but never started measures NOTHING, so the RAM arm would never fire.
    """
    from daemon.services.ram_guard.guard import RamGuard

    guard = RamGuard(
        enabled=True,
        resolved_ceiling=(CEILING_BYTES, "test"),
        poll_interval_ms=100,
        grace_ms=100,
        pss_confirm_deadline_ms=250,
    )
    set_ram_guard(guard)
    guard.start()
    try:
        yield guard
    finally:
        guard.stop()
        guard.join(3.0)
        set_ram_guard(None)



# A tiny ceiling so a small ballooning tree breaches cheaply.
CEILING_BYTES = 16 * 1024 * 1024
CHILD_BYTES = 8 * 1024 * 1024
CHILD_COUNT = 2
PLANNED_BYTES = CHILD_BYTES * CHILD_COUNT


def _mem_available_bytes() -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except OSError:
        return 0
    return 0


def _norm_ws(text: str) -> str:
    """Normalized-whitespace equality (D-6) — the pin comparator for the
    verbatim §3.5 sentences (markdown emphasis/backticks are already
    normalized away in the constants themselves)."""
    return " ".join((text or "").split())


def _make_guard(ceiling_bytes: int) -> RamGuard:
    """A guard with an injected ceiling — NOT started.

    The started variant is the ``real_guard`` fixture; this helper is
    only used where the test asserts the SWITCH-OFF posture (no
    measurement happens at all, so the threads are irrelevant).
    """
    return RamGuard(resolved_ceiling=(ceiling_bytes, "test"))


def _balloon_code(n_children: int = CHILD_COUNT) -> str:
    """Python source that FORKS ``n_children`` children which each TOUCH
    ``CHILD_BYTES``.

    ``bytearray(n)`` alone does NOT fault the pages (calloc ⇒ lazily
    mapped, RSS unchanged), so a balloon that only allocates never
    breaches — the pages must be WRITTEN.  The parent's ``os.fork()``
    returns a truthy pid ⇒ the parent skips the allocation and exits,
    leaving the children as the (orphaned) group members.
    """
    return (
        "import os,time\n"
        "def b():\n"
        f"    x=bytearray({CHILD_BYTES})\n"
        f"    for o in range(0,{CHILD_BYTES},4096): x[o]=1\n"
        "    time.sleep(120)\n"
        f"[os.fork() or b() for _ in range({n_children})]\n"
    )


def _balloon_command() -> str:
    """The direct-child shape: the command forks its own ballooners."""
    return f"{sys.executable} -u -c {shlex.quote(_balloon_code())}"


def _leaderless_command() -> str:
    """The leaderless shape (leader ruling Q2): a shell that backgrounds a
    ballooning grandchild and exits — the grandchild stays in the group."""
    inner = f"{sys.executable} -u -c {shlex.quote(_balloon_code())}"
    return f"sh -c {shlex.quote('(' + inner + ' &) ; sleep 0.1')}"


# ── Switch consistency with the bash lane ──────────────────────────────────


class TestSwitchConsistency:
    def test_switch_name_and_off_values_match_bash(self):
        bash_mod = importlib.import_module("daemon.tools.bash")

        assert (
            proc_tools_module.RAM_GUARD_ENV_SWITCH
            == bash_mod.RAM_GUARD_ENV_SWITCH
        )
        assert (
            proc_tools_module.RAM_GUARD_SWITCH_OFF_VALUES
            == bash_mod.RAM_GUARD_SWITCH_OFF_VALUES
        )

    @pytest.mark.parametrize("value", ["0", "false", "NO", "Off", "off", "  0  "])
    def test_off_values_agree(self, monkeypatch, value):
        # Compare the proc parse with the bash parse over a matrix.
        bash_mod = importlib.import_module("daemon.tools.bash")

        environ = {"ENSEMBLE_RAM_GUARD": value}
        assert proc_tools_module._ram_guard_switch(environ) == bash_mod._ram_guard_switch(environ)

    @pytest.mark.parametrize("value", [None, "", "1", "true", "yes", "on"])
    def test_on_values_agree(self, monkeypatch, value):
        bash_mod = importlib.import_module("daemon.tools.bash")

        environ = {} if value is None else {"ENSEMBLE_RAM_GUARD": value}
        assert proc_tools_module._ram_guard_switch(environ) == bash_mod._ram_guard_switch(environ)


# ── LLM-facing schema unchanged ─────────────────────────────────────────────


class TestSchemaUnchanged:
    def test_proc_run_schema_has_no_ceiling_parameter(self, unique_instance_id):
        from daemon.tools.proc_tools import create_proc_tools

        tools = create_proc_tools(current_instance_id=unique_instance_id)
        proc_run = next(t for t in tools if t.name == "proc_run")
        schema_keys = set(proc_run.args.keys())
        assert schema_keys == {"command", "workdir", "timeout"}, (
            f"proc_run LLM-facing schema changed: {schema_keys}"
        )


# ── user_stopped surfaced ──────────────────────────────────────────────────


class TestUserStoppedSurface:
    @pytest.mark.asyncio
    async def test_user_stopped_surfaced(self, proc_tools):
        tools, _ = proc_tools
        proc_run = tools["proc_run"]
        proc_status = tools["proc_status"]
        proc_stop = tools["proc_stop"]

        result = await proc_run.ainvoke({
            "command": f"{sys.executable} -u -c \"import time; time.sleep(30)\"",
        })
        assert "Error:" not in result
        m = re.search(r"(proc-[0-9a-f]+)", result)
        assert m
        process_id = m.group(1)
        await asyncio.sleep(0.5)
        await proc_stop.ainvoke({"process_id": process_id})
        await asyncio.sleep(0.5)
        status = await proc_status.ainvoke({"process_id": process_id})
        assert "user_stopped: true" in status
        assert "ram_killed: true" not in status


# ── no subreaper introduced ─────────────────────────────────────────────────


class TestNoSubreaper:
    def test_no_subreaper_introduced(self):
        repo_root = Path(__file__).resolve().parents[2]
        daemon_root = repo_root / "daemon"
        offenders = []
        for py in sorted(daemon_root.rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            text = py.read_text(encoding="utf-8", errors="replace")
            if "PR_SET_CHILD_SUBREAPER" in text:
                offenders.append(f"{py.relative_to(repo_root)}: PR_SET_CHILD_SUBREAPER")
            if "signal.SIGCHLD" in text:
                offenders.append(f"{py.relative_to(repo_root)}: signal.SIGCHLD")
        assert offenders == []

    def test_item13_sentence_present(self):
        src = Path("daemon/tools/proc_tools.py").read_text()
        assert "direct-child reap." in src


# ── P6 absent-stamp coupling (substrate-level, via the ladder) ──────────────


class TestP6AbsentStamp:
    def test_absent_stamp_proceeds_and_counts(self, tmp_path):
        from daemon.services.ram_guard import set_ram_guard
        from daemon.services.ram_guard.ladder import ram_kill_ladder
        from tests.unit.services.test_ram_guard_ladder import (
            build_tree, SYNTHETIC_PGID, LEADER_START,
        )

        set_ram_guard(None)
        root = build_tree(tmp_path / "proc", environ=b"PATH=/bin\\x00OTHER=1\\x00")
        unverified = []
        status, reason = ram_kill_ladder(
            SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root,
            captured_pgid=SYNTHETIC_PGID,
            tracking_id="t-1", on_ownership_unverified=lambda: unverified.append(1),
        )
        assert status == "killed", (status, reason)
        assert unverified == [1]


# ── Byte-identical proc_status block for a non-RAM process ──────────────────


class TestUnderLimitByteIdentical:
    @pytest.mark.asyncio
    async def test_the_proc_status_block_matches_the_base_commit_golden(
        self, proc_tools, recording_guard
    ):
        """D-3(ii) — the under-limit block is a STORED BASE-COMMIT GOLDEN.

        The golden is the base commit's (``829903f1b``) ``get_status``
        output shape for a cleanly-exited process, captured live on that
        renderer; the two run-varying fields (``process_id``, ``pid``)
        are interpolated and nothing else is.  A RAM line inserted
        anywhere — middle or tail — breaks the equality, so this is a
        byte-identity assertion, NOT a key-set proxy.
        """
        tools, _ = proc_tools
        proc_run = tools["proc_run"]
        proc_status = tools["proc_status"]

        result = await proc_run.ainvoke({
            "command": f"{sys.executable} -u -c \"print('hi')\"",
        })
        assert "Error:" not in result
        m = re.search(r"(proc-[0-9a-f]+)", result)
        assert m
        process_id = m.group(1)
        # Wait for exit.
        for _ in range(20):
            st = await proc_status.ainvoke({"process_id": process_id})
            if "status: exited" in st or "status: killed" in st:
                break
            await asyncio.sleep(0.2)
        status = await proc_status.ainvoke({"process_id": process_id})
        assert "status: exited" in status
        # No RAM surface lines for a non-RAM process.
        assert "ram_breach:" not in status
        assert "ram_killed: true" not in status
        assert "ram_kill_refused:" not in status
        assert "ram_breach_observed:" not in status

        # THE GOLDEN — byte-for-byte (the two varying fields
        # interpolated; everything else is the base commit's renderer).
        pid = re.search(r"^pid: (\d+)$", status, flags=re.M).group(1)
        expected = "\n".join([
            f"process_id: {process_id}",
            "status: exited",
            f"pid: {pid}",
            "exit_code: 0",
            f"buffer_bytes: 3 (limit={proc_tools_module._MEMORY_BUFFER_LIMIT_BYTES})",
        ])
        assert status == expected, (
            "proc_status block drifted from the base-commit golden:\n"
            f"--- expected ---\n{expected}\n--- actual ---\n{status}"
        )


# ── Real ballooning tree via proc_run (a) ───────────────────────────────────


class TestBallooningTree:
    @pytest.mark.asyncio
    @pytest.mark.timeout(90)
    async def test_ballooning_tree_via_proc_run_is_killed(self, proc_tools, real_guard):
        if _mem_available_bytes() < PLANNED_BYTES + 256 * 1024 * 1024:
            pytest.skip("insufficient memory")
        # The guard is installed AND started by the ``real_guard``
        # fixture — its two threads own the measurement, the breach
        # decision, the kill and the verdict (leader ruling Q1).
        tools, _ = proc_tools
        proc_run = tools["proc_run"]
        proc_status = tools["proc_status"]

        cmd = _balloon_command()
        result = await proc_run.ainvoke({"command": cmd, "timeout": 30})
        assert "Error:" not in result, f"proc_run failed: {result}"
        m = re.search(r"(proc-[0-9a-f]+)", result)
        assert m
        process_id = m.group(1)

        # Poll proc_status for the verdict.  Only a TERMINAL outcome ends the
        # poll: ``ram_killed: true`` (the guard killed the tree) or
        # ``ram_kill_refused:`` (the ladder refused — a verdict too, and
        # it must never be swallowed).  The PENDING surface
        # (``ram_breach_observed:`` + the peak) means the decision is
        # still in flight — it does NOT end the poll.
        deadline = asyncio.get_event_loop().time() + 15.0
        saw_ram = False
        st = ""
        while asyncio.get_event_loop().time() < deadline:
            st = await proc_status.ainvoke({"process_id": process_id})
            if "ram_killed: true" in st or "ram_kill_refused:" in st:
                saw_ram = True
                break
            await asyncio.sleep(0.3)
        assert saw_ram, f"Never saw RAM verdict in proc_status: {st}"
        status = await proc_status.ainvoke({"process_id": process_id})
        assert "ram_breach:" in status
        assert "ram_killed: true" in status
        # The lane-aware P6's POSITIVE signal (leader ruling 2026-11-11),
        # proven END-TO-END: the guard's ladder read the proc lane's OWN
        # stamp (present AND matching) ⇒ ownership VERIFIED — the
        # ``ownership_unverified`` counter must NOT have moved.
        assert real_guard.counters_snapshot()["ownership_unverified"] == 0, (
            "a proc-lane kill must VERIFY the lane's own stamp, not fall "
            "back to the unverifiable-proceed path"
        )
        # D-3(i) — the plan's T11 acceptance says the WHOLE TREE is
        # killed; probe it for real instead of trusting the verdict
        # line: a liveness probe on the captured pgid must report the
        # group GONE (signal 0 is a liveness probe, not a signal).
        from daemon.tools.proc_tools import get_background_process_manager

        manager = get_background_process_manager()
        info = manager._processes[_][process_id]
        assert info.ram_pgid > 0
        try:
            os.killpg(info.ram_pgid, 0)
            group_alive = True
        except ProcessLookupError:
            group_alive = False
        except PermissionError:  # pragma: no cover — a live member we may not probe
            group_alive = True
        assert not group_alive, (
            f"the process group {info.ram_pgid} still has a live member "
            "after the RAM kill"
        )
        # The verdict's content-free fields ride the ``ram_breach:``
        # line: the resolved ceiling, the re-arm threshold beside it,
        # the honest-peak fields and the metric (arch §4.3).
        breach_line = next(
            ln for ln in status.splitlines() if ln.startswith("ram_breach:")
        )
        assert "limit_bytes=" in breach_line
        assert "rearm_threshold_bytes=" in breach_line
        assert "observed_peak_bytes=" in breach_line
        assert "peak_is_lower_bound=" in breach_line
        assert "under_report_bound_bytes=" in breach_line
        assert "metric=" in breach_line
        assert "trigger_metric=" in breach_line
        # The negative space: no argv, no env, no stdout anywhere.
        assert "command=" not in breach_line
        assert "environ" not in breach_line
        assert "stdout" not in breach_line


# ── Leaderless-tree shape (e) ───────────────────────────────────────────────


class TestLeaderlessTree:
    @pytest.mark.asyncio
    @pytest.mark.timeout(90)
    async def test_leaderless_tree_shape(self, proc_tools, real_guard):
        if _mem_available_bytes() < PLANNED_BYTES + 256 * 1024 * 1024:
            pytest.skip("insufficient memory")
        # The guard is installed AND started by the ``real_guard``
        # fixture (its two threads own the measurement + the kill).
        tools, _ = proc_tools
        proc_run = tools["proc_run"]
        proc_status = tools["proc_status"]

        cmd = _leaderless_command()
        result = await proc_run.ainvoke({"command": cmd, "timeout": 30})
        assert "Error:" not in result, f"proc_run failed: {result}"
        m = re.search(r"(proc-[0-9a-f]+)", result)
        assert m
        process_id = m.group(1)

        # The leader (the shell) exits fast; the grandchild balloons.
        # The watch MUST survive the leader's exit while a group member
        # is live (phase2-plan.md T10 — the guard owns the watch's END).
        deadline = asyncio.get_event_loop().time() + 15.0
        saw_ram = False
        st = ""
        while asyncio.get_event_loop().time() < deadline:
            st = await proc_status.ainvoke({"process_id": process_id})
            if "ram_killed: true" in st or "ram_kill_refused:" in st:
                saw_ram = True
                break
            await asyncio.sleep(0.3)
        # The POSITIVE shape: the guard killed the tree (the grandchild
        # included) and the verdict rides proc_status.
        # The POSITIVE shape: the guard killed the tree (the grandchild
        # included) and the verdict rides proc_status.
        assert saw_ram, f"Never saw RAM verdict in proc_status: {st}"
        assert "ram_killed: true" in st
        # D-2 — the rendered block must be INTERNALLY CONSISTENT in the
        # post-return shape: the direct child exited 0 (so the drain
        # finalized ``exited`` long before the kill landed), yet the
        # rendered status must read ``killed`` — never ``exited`` above
        # ``ram_killed: true``.
        assert "status: killed" in st, f"stale post-return status: {st}"
        assert "status: exited" not in st, f"self-contradicting block: {st}"
        # The guard's own counters prove the KILL was delivered by the
        # guard's execution-lane thread (not by the proc lane).
        assert real_guard.counters_snapshot()["ram_kills"] >= 1
        assert real_guard.counters_snapshot()["ram_breaches_detected"] >= 1
        # The lane-aware P6 verified the proc lane's OWN stamp (present
        # AND matching) — the unverifiable counter did not move.
        assert real_guard.counters_snapshot()["ownership_unverified"] == 0


# ── Switch OFF (c) ──────────────────────────────────────────────────────────


class TestSwitchOff:
    @pytest.mark.asyncio
    async def test_switch_off_no_extra_task_and_no_registration(self, proc_tools, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_RAM_GUARD", "0")
        guard = _make_guard(CEILING_BYTES)
        set_ram_guard(guard)
        try:
            tools, _ = proc_tools
            proc_run = tools["proc_run"]
            proc_status = tools["proc_status"]

            result = await proc_run.ainvoke({
                "command": f"{sys.executable} -u -c \"import time; time.sleep(5)\"",
            })
            assert "Error:" not in result
            m = re.search(r"(proc-[0-9a-f]+)", result)
            assert m
            process_id = m.group(1)
            await asyncio.sleep(1.0)
            status = await proc_status.ainvoke({"process_id": process_id})
            # No RAM surface lines with the switch OFF.
            assert "ram_breach:" not in status
            assert "ram_killed: true" not in status
            # The guard registry must be untouched for this spawn.
            assert guard.counters_snapshot().get("ram_breaches_detected", 0) == 0
        finally:
            set_ram_guard(None)
            monkeypatch.delenv("ENSEMBLE_RAM_GUARD", raising=False)


# ── Negative paths (d) ──────────────────────────────────────────────────────


class TestNegativePaths:
    def test_non_linux_platform_guard_refuses(self, tmp_path):
        # The ladder's P0 refuses on non-Linux platforms.  We exercise the
        # ladder directly with platform="win32".
        from daemon.services.ram_guard.ladder import ram_kill_ladder
        from tests.unit.services.test_ram_guard_ladder import (
            build_tree, SYNTHETIC_PGID, LEADER_START,
        )

        root = build_tree(tmp_path / "proc", environ=b"ENSEMBLE_BASH_TRACKING_ID=t-1\\x00")
        status, reason = ram_kill_ladder(
            12345, 1000, 0.25, proc_root=root, captured_pgid=12345,
            tracking_id="t-1", platform="win32",
        )
        assert status == "refused"
        assert reason == "refused:platform-unsupported"

    def test_unreadable_proc_proceeds_and_counts(self, tmp_path):
        from daemon.services.ram_guard.ladder import ram_kill_ladder
        from tests.unit.services.test_ram_guard_ladder import (
            LEADER_START,
            SYNTHETIC_PGID,
            build_tree,
        )

        root = build_tree(tmp_path / "proc", environ=None)  # no environ file
        unverified = []
        status, _ = ram_kill_ladder(
            SYNTHETIC_PGID, LEADER_START, 0.25, proc_root=root,
            captured_pgid=SYNTHETIC_PGID,
            tracking_id="t-1", on_ownership_unverified=lambda: unverified.append(1),
        )
        assert status == "killed"
        assert unverified == [1]

    @pytest.mark.asyncio
    async def test_proc_list_header_byte_identical(self, proc_tools):
        tools, _ = proc_tools
        proc_run = tools["proc_run"]
        proc_list = tools["proc_list"]
        await proc_run.ainvoke({"command": f"{sys.executable} -c \"print('x')\""})
        out = await proc_list.ainvoke({})
        # The proc_list header is one of the FOUR never-changing strings
        # (arch §6 / the DQ6 census row 14).  Pin it CHARACTER-FOR-
        # CHARACTER, not as a loose substring.
        assert "process_id | status | command | uptime" in out
        assert "---|---|---|---" in out


# ── T1 — the internal ceiling seam (no LLM-facing parameter) ───────────────


class TestInternalCeilingSeam:
    @pytest.mark.asyncio
    async def test_the_internal_ceiling_seam_is_honoured(
        self, unique_instance_id, recording_guard
    ):
        """T1 — an explicit INTERNAL ceiling reaches the registration."""
        from daemon.tools.proc_tools import get_background_process_manager

        manager = get_background_process_manager()
        process_id, err = await manager.start_process(
            instance_id=unique_instance_id,
            command=f"{sys.executable} -c \"print('x')\"",
            workdir=None,
            timeout_seconds=0,
            ram_ceiling_bytes=12345678,
        )
        try:
            assert err is None
            assert len(recording_guard.registered) == 1
            assert recording_guard.registered[0]["ceiling_bytes"] == 12345678
        finally:
            await manager.cleanup_instance(unique_instance_id)

    @pytest.mark.asyncio
    async def test_the_default_path_uses_the_guard_resolved_ceiling(
        self, unique_instance_id, recording_guard
    ):
        """T1 — the default path passes NO override: the guard resolves."""
        from daemon.tools.proc_tools import get_background_process_manager

        manager = get_background_process_manager()
        process_id, err = await manager.start_process(
            instance_id=unique_instance_id,
            command=f"{sys.executable} -c \"print('x')\"",
            workdir=None,
            timeout_seconds=0,
        )
        try:
            assert err is None
            assert len(recording_guard.registered) == 1
            assert recording_guard.registered[0]["ceiling_bytes"] is None
        finally:
            await manager.cleanup_instance(unique_instance_id)


# ── T2 — the kill-switch: OFF ⇒ no registration, no extra task ─────────────


class TestKillSwitch:
    @pytest.mark.asyncio
    async def test_switch_off_registers_nothing(
        self, unique_instance_id, recording_guard, monkeypatch
    ):
        monkeypatch.setenv("ENSEMBLE_RAM_GUARD", "0")
        from daemon.tools.proc_tools import get_background_process_manager

        manager = get_background_process_manager()
        process_id, err = await manager.start_process(
            instance_id=unique_instance_id,
            command=f"{sys.executable} -c \"print('x')\"",
            workdir=None,
            timeout_seconds=0,
        )
        try:
            assert err is None
            assert recording_guard.registered == []
        finally:
            await manager.cleanup_instance(unique_instance_id)
            monkeypatch.delenv("ENSEMBLE_RAM_GUARD", raising=False)

    @pytest.mark.asyncio
    async def test_the_task_set_is_exactly_three_with_the_switch_off(
        self, unique_instance_id, recording_guard, monkeypatch
    ):
        """T2 — the task set is UNCHANGED (reader + exit + timeout)."""
        monkeypatch.setenv("ENSEMBLE_RAM_GUARD", "0")
        from daemon.tools.proc_tools import get_background_process_manager

        manager = get_background_process_manager()
        process_id, err = await manager.start_process(
            instance_id=unique_instance_id,
            command=f"{sys.executable} -c \"print('x')\"",
            workdir=None,
            timeout_seconds=5,
        )
        try:
            assert err is None
            info = manager._processes[unique_instance_id][process_id]
            task_fields = sorted(
                f for f in info.__dataclass_fields__ if f.endswith("_task")
            )
            assert task_fields == ["exit_task", "reader_task", "timeout_task"]
            assert isinstance(info.reader_task, asyncio.Task)
            assert isinstance(info.exit_task, asyncio.Task)
            assert isinstance(info.timeout_task, asyncio.Task)
        finally:
            await manager.cleanup_instance(unique_instance_id)
            monkeypatch.delenv("ENSEMBLE_RAM_GUARD", raising=False)


# ── T3/T4/T9 — the registration + the lane's shape ─────────────────────────


class TestLaneShape:
    @pytest.mark.asyncio
    async def test_the_guard_registry_holds_the_entry(
        self, unique_instance_id, recording_guard
    ):
        """T3 — the proc lane REGISTERS with the guard (and nothing else)."""
        from daemon.tools.proc_tools import get_background_process_manager

        manager = get_background_process_manager()
        process_id, err = await manager.start_process(
            instance_id=unique_instance_id,
            command=f"{sys.executable} -c \"print('x')\"",
            workdir=None,
            timeout_seconds=0,
        )
        try:
            assert err is None
            assert len(recording_guard.registered) == 1
            reg = recording_guard.registered[0]
            assert reg["lane"] == "proc"
            assert reg["instance_id"] == unique_instance_id
            assert reg["pid"] > 0
            # T9 — the pgid is captured AT SPAWN: ``start_new_session=True``
            # makes PGID == PID, and the ladder receives the CAPTURED
            # value (never a re-derived one).
            assert reg["pgid"] == reg["pid"]
            assert reg["start_time_jiffies"] is not None
            info = manager._processes[unique_instance_id][process_id]
            assert info.ram_pgid == reg["pgid"]
            assert info.ram_start_time_jiffies == reg["start_time_jiffies"]
            # The lane-aware P6's POSITIVE signal (leader ruling
            # 2026-11-11): the guard's tracking id IS the value the lane
            # stamped under the lane's OWN variable, so P6 finds it
            # present AND matching ⇒ VERIFIED.
            assert reg["tracking_id"] == process_id
            assert info.ram_tracking_id == process_id
            assert info.tracking_id == process_id
        finally:
            await manager.cleanup_instance(unique_instance_id)

    @pytest.mark.asyncio
    async def test_the_lane_creates_no_fourth_task(
        self, unique_instance_id, recording_guard
    ):
        """T4 — there is NO fourth asyncio task (leader ruling Q1)."""
        from daemon.tools.proc_tools import get_background_process_manager

        manager = get_background_process_manager()
        process_id, err = await manager.start_process(
            instance_id=unique_instance_id,
            command=f"{sys.executable} -c \"print('x')\"",
            workdir=None,
            timeout_seconds=5,
        )
        try:
            assert err is None
            info = manager._processes[unique_instance_id][process_id]
            task_fields = sorted(
                f for f in info.__dataclass_fields__ if f.endswith("_task")
            )
            assert task_fields == ["exit_task", "reader_task", "timeout_task"]
        finally:
            await manager.cleanup_instance(unique_instance_id)

    @pytest.mark.asyncio
    @pytest.mark.timeout(90)
    async def test_the_ram_kill_is_not_delivered_by_the_proc_lane(
        self, proc_tools, real_guard, monkeypatch
    ):
        """T4 — the guard thread delivers the kill, NOT ``_attempt_kill_signal``.

        The proc lane's ``_attempt_kill_signal`` is NOT the RAM delivery
        path (arch DQ4): it re-derives the pgid at kill time (the TOCTOU
        the capture block already fixed) and its environ gate is
        fail-open.  The guard's execution-lane thread signals the group
        through the DQ4 ladder.
        """
        if _mem_available_bytes() < PLANNED_BYTES + 256 * 1024 * 1024:
            pytest.skip("insufficient memory")
        from daemon.tools.proc_tools import BackgroundProcessManager

        calls: list[str] = []
        original = BackgroundProcessManager._attempt_kill_signal

        def spy(self, info, *args, **kwargs):
            calls.append(info.process_id)
            return original(self, info, *args, **kwargs)

        monkeypatch.setattr(BackgroundProcessManager, "_attempt_kill_signal", spy)

        tools, _ = proc_tools
        proc_run = tools["proc_run"]
        proc_status = tools["proc_status"]
        cmd = _balloon_command()
        result = await proc_run.ainvoke({"command": cmd, "timeout": 30})
        assert "Error:" not in result, f"proc_run failed: {result}"
        m = re.search(r"(proc-[0-9a-f]+)", result)
        assert m
        process_id = m.group(1)

        deadline = asyncio.get_event_loop().time() + 15.0
        st = ""
        while asyncio.get_event_loop().time() < deadline:
            st = await proc_status.ainvoke({"process_id": process_id})
            if "ram_killed: true" in st or "ram_kill_refused:" in st:
                break
            await asyncio.sleep(0.3)
        assert "ram_killed: true" in st, f"no RAM verdict: {st}"
        assert calls == [], (
            "the RAM kill must be delivered by the guard thread, not by "
            f"the proc lane ({calls})"
        )
        # The guard's own counters prove the guard delivered the kill.
        assert real_guard.counters_snapshot()["ram_kills"] >= 1


# ── T5 — the verdict is consulted BEFORE the status is finalized ────────────


class TestVerdictOrdering:
    @pytest.mark.asyncio
    async def test_a_ram_kill_reports_killed_and_ram_killed_true(
        self, unique_instance_id
    ):
        """T5 — the RAM verdict wins over the ordinary exit.

        The process exits NORMALLY (``exit_code == 0``).  If the status
        were finalized BEFORE the verdict was consulted, the status would
        read ``exited``; the verdict says ``KILLED`` ⇒ the status reads
        ``killed`` AND ``ram_killed`` is True.  The ORDERING is therefore
        observable, not assumed.
        """
        from daemon.tools.proc_tools import get_background_process_manager

        record = _make_record()
        guard = _RecordingGuard(record, publish_after_reads=1)
        set_ram_guard(guard)
        manager = get_background_process_manager()
        try:
            process_id, err = await manager.start_process(
                instance_id=unique_instance_id,
                command=f"{sys.executable} -c \"print('x')\"",
                workdir=None,
                timeout_seconds=0,
            )
            assert err is None
            info = manager._processes[unique_instance_id][process_id]
            for _ in range(50):
                if info.status in ("exited", "killed"):
                    break
                await asyncio.sleep(0.1)
            assert info.exit_code == 0, "the command exited normally"
            assert info.status == "killed"
            assert info.ram_killed is True
        finally:
            set_ram_guard(None)
            await manager.cleanup_instance(unique_instance_id)

    @pytest.mark.asyncio
    async def test_a_user_stopped_process_is_distinguishable(
        self, proc_tools
    ):
        """T5 — a USER stop reads ``killed`` with ``ram_killed`` False."""
        tools, _ = proc_tools
        proc_run = tools["proc_run"]
        proc_status = tools["proc_status"]
        proc_stop = tools["proc_stop"]

        result = await proc_run.ainvoke({
            "command": f"{sys.executable} -u -c \"import time; time.sleep(30)\"",
        })
        assert "Error:" not in result
        m = re.search(r"(proc-[0-9a-f]+)", result)
        assert m
        process_id = m.group(1)
        await asyncio.sleep(0.5)
        await proc_stop.ainvoke({"process_id": process_id})
        await asyncio.sleep(0.5)
        status = await proc_status.ainvoke({"process_id": process_id})
        assert "status: killed" in status
        assert "user_stopped: true" in status
        assert "ram_killed: true" not in status


# ── T8 — the release on every exit path ────────────────────────────────────


class TestReleasePaths:
    @pytest.mark.asyncio
    async def test_the_release_follows_the_publication(
        self, unique_instance_id
    ):
        """T8 — the release happens AFTER the verdict is published."""
        from daemon.tools.proc_tools import get_background_process_manager

        record = _make_record()
        guard = _RecordingGuard(record, publish_after_reads=1)
        set_ram_guard(guard)
        manager = get_background_process_manager()
        try:
            process_id, err = await manager.start_process(
                instance_id=unique_instance_id,
                command=f"{sys.executable} -c \"print('x')\"",
                workdir=None,
                timeout_seconds=0,
            )
            assert err is None
            info = manager._processes[unique_instance_id][process_id]
            for _ in range(50):
                if info.status in ("exited", "killed"):
                    break
                await asyncio.sleep(0.1)
            kinds = [e[0] for e in guard.events]
            assert "publication" in kinds, guard.events
            assert "release" in kinds, guard.events
            assert kinds.index("publication") < kinds.index("release"), (
                f"the release must follow the publication: {guard.events}"
            )
        finally:
            set_ram_guard(None)
            await manager.cleanup_instance(unique_instance_id)

    @pytest.mark.asyncio
    async def test_the_cleanup_path_releases_the_registration(
        self, unique_instance_id, recording_guard
    ):
        """T8 — ``cleanup_instance`` releases the registration."""
        from daemon.tools.proc_tools import get_background_process_manager

        manager = get_background_process_manager()
        process_id, err = await manager.start_process(
            instance_id=unique_instance_id,
            command=f"{sys.executable} -u -c \"import time; time.sleep(30)\"",
            workdir=None,
            timeout_seconds=0,
        )
        assert err is None
        await manager.cleanup_instance(unique_instance_id)
        assert recording_guard.release_calls, (
            "the cleanup path must release the guard registration"
        )

    @pytest.mark.asyncio
    async def test_the_release_is_idempotent(self, unique_instance_id, recording_guard):
        """T8 — a repeated cleanup does NOT raise and does not double-release."""
        from daemon.tools.proc_tools import get_background_process_manager

        manager = get_background_process_manager()
        process_id, err = await manager.start_process(
            instance_id=unique_instance_id,
            command=f"{sys.executable} -u -c \"import time; time.sleep(30)\"",
            workdir=None,
            timeout_seconds=0,
        )
        assert err is None
        await manager.cleanup_instance(unique_instance_id)
        first = len(recording_guard.release_calls)
        await manager.cleanup_instance(unique_instance_id)
        assert len(recording_guard.release_calls) == first

    @pytest.mark.asyncio
    async def test_the_cleanup_all_path_releases_the_registration(
        self, unique_instance_id, recording_guard
    ):
        """T8 — ``cleanup_all`` (the daemon-shutdown sweep) releases too."""
        from daemon.tools.proc_tools import get_background_process_manager

        manager = get_background_process_manager()
        process_id, err = await manager.start_process(
            instance_id=unique_instance_id,
            command=f"{sys.executable} -u -c \"import time; time.sleep(30)\"",
            workdir=None,
            timeout_seconds=0,
        )
        assert err is None
        await manager.cleanup_all()
        assert recording_guard.release_calls, (
            "the daemon-shutdown sweep must release the guard registration"
        )


# ── T10/T11 — the surfaces and the untouched paths ─────────────────────────


class TestUntouchedPaths:
    @pytest.mark.asyncio
    async def test_the_process_cap_is_unaffected(
        self, unique_instance_id, recording_guard
    ):
        """T11 — the ``MAX_PROCESSES_PER_INSTANCE`` cap still applies."""
        from daemon.tools.proc_tools import (
            MAX_PROCESSES_PER_INSTANCE,
            get_background_process_manager,
        )

        manager = get_background_process_manager()
        try:
            for _ in range(MAX_PROCESSES_PER_INSTANCE):
                process_id, err = await manager.start_process(
                    instance_id=unique_instance_id,
                    command=f"{sys.executable} -c \"print('x')\"",
                    workdir=None,
                    timeout_seconds=0,
                )
                assert err is None, err
            process_id, err = await manager.start_process(
                instance_id=unique_instance_id,
                command=f"{sys.executable} -c \"print('x')\"",
                workdir=None,
                timeout_seconds=0,
            )
            assert process_id is None
            assert err is not None and "Error" in err
            assert str(MAX_PROCESSES_PER_INSTANCE) in err
        finally:
            await manager.cleanup_instance(unique_instance_id)

    @pytest.mark.asyncio
    async def test_the_proc_status_block_is_byte_identical_for_a_non_ram_process(
        self, proc_tools, recording_guard
    ):
        """T7 — the RAM arm is ADDITIVE: the block never changes."""
        tools, _ = proc_tools
        proc_run = tools["proc_run"]
        proc_status = tools["proc_status"]

        result = await proc_run.ainvoke({
            "command": f"{sys.executable} -c \"print('x')\"",
        })
        assert "Error:" not in result
        m = re.search(r"(proc-[0-9a-f]+)", result)
        assert m
        process_id = m.group(1)
        for _ in range(20):
            st = await proc_status.ainvoke({"process_id": process_id})
            if "status: exited" in st:
                break
            await asyncio.sleep(0.2)
        lines = st.splitlines()
        # The block's line SET is exactly today's — the RAM lines are
        # APPENDED, never interleaved.
        assert [ln.split(":")[0] for ln in lines] == [
            "process_id",
            "status",
            "pid",
            "exit_code",
            "buffer_bytes",
        ]
        assert "ram_breach:" not in st
        assert "ram_killed: true" not in st


# ── D-4 — the spawn-window orphan path releases the registration ────────────


class TestSpawnWindowOrphanRelease:
    @pytest.mark.asyncio
    async def test_the_orphan_path_releases_the_registration(
        self, unique_instance_id, recording_guard
    ):
        """phase4-plan.md:67 — the spawn-window orphan path releases.

        The pre-existing C2 test (``test_proc_tools.py``) predates the
        release hook, so the plan's orphan leg had NO coverage.  This leg
        races ``cleanup_instance`` into the spawn window exactly as the
        C2 test does and asserts the T8 release hook FIRES on that path
        (``release_calls`` non-empty) — the registration never leaks when
        the spawn is orphaned mid-window.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from daemon.tools.proc_tools import get_background_process_manager

        manager = get_background_process_manager()

        async def slow_spawn(command, **kwargs):
            """The spawn window: sleep, clean up INSIDE it, then return
            a mock proc (the C2 shape, verbatim)."""
            await asyncio.sleep(0.1)
            await manager.cleanup_instance(unique_instance_id)
            mock_proc = MagicMock()
            mock_proc.pid = 99999  # non-existent pid
            mock_proc.returncode = None
            mock_proc.stdout = None  # reader exits immediately
            mock_proc.kill = MagicMock()
            mock_proc.wait = AsyncMock(return_value=-9)
            return mock_proc

        with patch(
            "daemon.tools.proc_tools.asyncio.create_subprocess_shell",
            new=slow_spawn,
        ):
            process_id, err = await manager.start_process(
                instance_id=unique_instance_id,
                command="sleep 100",
                workdir=None,
                timeout_seconds=0,
            )

        # The C2 contract: the spawn is reported as cleaned up.
        assert process_id is None
        assert err is not None and "Error" in err
        # THE PHASE-4 LEG: the release hook fired on the orphan path.
        assert recording_guard.registered, (
            "the spawn must have registered with the guard before the "
            "orphan path can release it"
        )
        assert recording_guard.release_calls, (
            "the spawn-window orphan path must release the guard "
            "registration (phase4-plan.md:67)"
        )


# ── P6 — the ownership-stamp isolation (the crux) ───────────────────────────


class TestOwnershipStampIsolation:
    """The proc child's env carries its OWN stamp and no FOREIGN one.

    The ladder's P6 is LANE-AWARE (leader ruling 2026-11-11): for a
    proc-lane spawn it reads the PROC lane's own variable
    (``ENSEMBLE_PROC_TRACKING_ID``), whose value IS the guard's tracking
    id ⇒ present AND matching ⇒ ownership VERIFIED (a positive signal,
    never counted as unverified).  A FOREIGN lane's stamp present would
    be a MISMATCH ⇒ refuse — so the proc lane strips the bash lane's
    variable from the child env: the daemon's own ambient environment
    carries an ``ENSEMBLE_BASH_TRACKING_ID``, and an inherited value
    would read as a REAL mismatch and refuse this lane's kill
    permanently.

    The stamp ABSENT (e.g. a legacy child) stays UNVERIFIABLE ⇒ P6
    PROCEEDS with ``ownership_unverified++`` (ABSENT ≠ MISMATCH).
    """

    def test_the_child_env_carries_no_foreign_ownership_stamp(self, monkeypatch):
        from daemon.services.ram_guard.ladder import OWNERSHIP_ENV_VAR

        monkeypatch.setenv(OWNERSHIP_ENV_VAR, "a-foreign-ambient-id")
        env = proc_tools_module._build_child_env("proc-abc12345")
        assert OWNERSHIP_ENV_VAR not in env, (
            "the proc child must not inherit a foreign lane's ownership "
            "stamp — it would read as a REAL mismatch in P6"
        )
        # The proc lane's OWN stamp is present and authoritative.
        assert env[proc_tools_module._TRACKING_ENV_VAR] == "proc-abc12345"

    def test_the_strip_is_a_no_op_without_the_ambient_stamp(self, monkeypatch):
        from daemon.services.ram_guard.ladder import OWNERSHIP_ENV_VAR

        monkeypatch.delenv(OWNERSHIP_ENV_VAR, raising=False)
        env = proc_tools_module._build_child_env("proc-abc12345")
        assert OWNERSHIP_ENV_VAR not in env
        assert env[proc_tools_module._TRACKING_ENV_VAR] == "proc-abc12345"

    def test_the_lane_var_names_agree_across_the_boundary(self):
        """The substrate's per-lane stamp names match the lanes' own.

        The substrate duplicates the literals (the import freeze forbids
        ``ram_guard → daemon/tools``); the coupling is pinned HERE so a
        rename on either side cannot silently blind the P6 check.
        """
        from daemon.services.bash_spawn_reaper import BASH_TRACKING_ENV_VAR
        from daemon.services.ram_guard.ladder import OWNERSHIP_ENV_VAR_BY_LANE

        assert OWNERSHIP_ENV_VAR_BY_LANE["proc"] == (
            proc_tools_module._TRACKING_ENV_VAR
        )
        assert OWNERSHIP_ENV_VAR_BY_LANE["bash"] == BASH_TRACKING_ENV_VAR


# ── T7 — the three mandatory disclosures + the pinned lines ────────────────


class TestForeignStampSmuggling:
    """Leader ruling B: an env overlay cannot forge the OTHER lane's stamp."""

    def test_the_overlay_cannot_smuggle_a_foreign_stamp(self, monkeypatch):
        from daemon.services.ram_guard.ladder import OWNERSHIP_ENV_VAR
        from daemon.tools import proc_tools as pt

        monkeypatch.setenv(OWNERSHIP_ENV_VAR, "a-foreign-ambient-id")
        env = pt._build_child_env(
            "proc-abc12345",
            process_env_overlay={OWNERSHIP_ENV_VAR: "forged-via-overlay"},
        )
        # The foreign stamp is stripped POST-overlay.
        assert OWNERSHIP_ENV_VAR not in env, (
            "overlay-smuggled foreign stamp survived the post-overlay strip"
        )
        # The own stamp is present and authoritative.
        assert env[pt._TRACKING_ENV_VAR] == "proc-abc12345"

    def test_the_overlay_cannot_clobber_the_own_stamp(self, monkeypatch):
        from daemon.services.ram_guard.ladder import OWNERSHIP_ENV_VAR
        from daemon.tools import proc_tools as pt

        env = pt._build_child_env(
            "proc-abc12345",
            process_env_overlay={pt._TRACKING_ENV_VAR: "forged-via-overlay"},
        )
        assert env[pt._TRACKING_ENV_VAR] == "proc-abc12345", (
            "the own stamp must be re-asserted last so the overlay cannot "
            "clobber it (a REAL mismatch would follow)"
        )
        assert OWNERSHIP_ENV_VAR not in env


class TestDisclosures:
    def test_the_item13_statement_is_verbatim_in_the_lane_docstring(self):
        """T12/D-6 — the §3.5 item-13 statement is VERBATIM in the lane's
        module docstring (normalized whitespace; the markdown emphasis
        and backticks of the plan text normalized away).  The runbook
        carry is Phase 7's.
        """
        assert _norm_ws(proc_tools_module.RAM_ITEM13_SENTENCE) in _norm_ws(
            proc_tools_module.__doc__ or ""
        )
        assert _norm_ws("No subreaper is introduced") in _norm_ws(
            proc_tools_module.__doc__ or ""
        )

    def test_the_item12_sentence_is_verbatim_in_the_status_docstring(self):
        """T7(b)/D-6 — the §3.5 item-12 sentence is VERBATIM in the
        ``proc_status`` LLM-facing docstring AND the module docstring
        (normalized whitespace)."""
        tools = {t.name: t for t in create_proc_tools(current_instance_id="inst-doc")}
        doc = tools["proc_status"]._full_doc_
        assert _norm_ws(proc_tools_module.RAM_ITEM12_SENTENCE) in _norm_ws(doc)
        assert "QUERY-ONLY" in doc
        assert "ram_breach:" in doc
        assert "ram_killed: true" in doc
        assert _norm_ws(proc_tools_module.RAM_ITEM12_SENTENCE) in _norm_ws(
            proc_tools_module.__doc__ or ""
        )

    def test_the_surface_line_names_are_pinned(self):
        """T7 (c) — the pinned line names (the DQ6 census)."""
        from daemon.services.ram_guard import errors as errors_mod

        assert errors_mod.PROC_BREACH_LINE_PREFIX == "ram_breach: "
        assert errors_mod.PROC_KILLED_LINE == "ram_killed: true"
        assert errors_mod.PROC_REFUSAL_LINE == "ram_kill_refused: {reason}"
        assert errors_mod.PROC_BREACH_OBSERVED_LINE == "ram_breach_observed: true"
        assert errors_mod.PROC_PEAK_LINE == "ram_observed_peak_bytes: {peak_bytes}"

    def test_the_pending_peak_line_mirrors_the_bash_lane_verbatim(self):
        """The pending-surface peak line mirrors bash's name verbatim."""
        bash_module = importlib.import_module("daemon.tools.bash")
        from daemon.services.ram_guard import errors as errors_mod

        assert errors_mod.PROC_PEAK_LINE == bash_module.RAM_PEAK_LINE
        assert errors_mod.PROC_REFUSAL_LINE == bash_module.RAM_REFUSAL_LINE
        assert errors_mod.PROC_BREACH_OBSERVED_LINE == bash_module.RAM_BREACH_LINE

    def test_the_proc_lane_writes_no_durable_registry_row(self):
        """DQ5 — the proc lane registers with the GUARD only."""
        src = Path("daemon/tools/proc_tools.py").read_text()
        assert "bash_spawn_registry" not in src
        assert "pid_registry" not in src

    def test_the_coincident_timeout_flag_is_bool_timed_out(self):
        """T5 — the coincidence flag's semantics are ``bool(timed_out)``.

        The dispatcher ruling (2026-11-11): the coincidence is reported
        when the timeout arm ALSO fired — NOT when the guard "wins" the
        race.  The flag rides the ``ram_breach:`` line, so the ruling is
        observable in this lane.
        """
        from daemon.services.ram_guard import errors as errors_mod

        record = _make_record(
            state=STATE_KILLED,
            reason="killed:ladder",
            breach_at=time.time(),
            killed_at=time.time(),
        )
        not_coincident = errors_mod.format_ram_proc_lines(record, timed_out=False)
        assert any("coincident_timeout=false" in ln for ln in not_coincident)
        coincident = errors_mod.format_ram_proc_lines(record, timed_out=True)
        assert any("coincident_timeout=true" in ln for ln in coincident)

    def test_the_pending_surface_is_reported_as_pending(self):
        """A breach that is NOT yet terminal is reported as PENDING.

        The decision is never fabricated: the pending surface carries
        ``ram_breach_observed: true`` + the observed peak (the bash
        lane's ``ram_observed_peak_bytes`` name, verbatim) and NO
        ``ram_killed: true``.
        """
        from daemon.services.ram_guard import errors as errors_mod

        record = _make_record(state=STATE_WATCHING, breach_at=time.time())
        lines = errors_mod.format_ram_proc_lines(record)
        assert "ram_breach_observed: true" in lines
        assert any(ln.startswith("ram_observed_peak_bytes: ") for ln in lines)
        assert "ram_killed: true" not in lines


class TestBoundedWaitDeadline:
    """Cure 4 — the proc lane's bounded-wait floor is the §18.4
    derivation (PROBE_MS declared at 29; the 10 ms estimate withdrawn)."""

    def test_the_proc_deadline_floor_matches_the_declared_probe(self):
        assert proc_tools_module.RAM_GUARD_PROBE_MS == 29
        assert proc_tools_module.RAM_GUARD_WAIT_DEADLINE_MS == (
            2 * (250 + 250 + 29) + 250
        ) == 1308

    def test_a_bare_guard_falls_back_to_the_1308_floor(self):
        class _BareGuard:
            pass

        assert proc_tools_module._ram_wait_deadline_ms(_BareGuard()) == 1308


import pytest

@pytest.fixture(autouse=True)
def _ensemble32_linux_platform(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
