"""Bash-lane RAM auto-termination tests (phase3-plan.md T14).

The pack covers the five shapes the plan names:

* (a) a REAL ballooning tree under a small INJECTED ceiling — the whole
  tree dies and the return is the typed envelope whose DECIDING value
  clears the ceiling;
* (b) an under-limit command — byte-identical to the BASE COMMIT;
* (c) the kill-switch OFF path — a complete no-op, byte-identical;
* (d) the negative paths — a non-Linux platform guard, an unreadable
  ``/proc``, an enumeration failure;
* (e) the leaderless-tree shape.

Plus the lane contracts the plan pins: the SECURITY ROUTING (the
LLM-facing schema gains NO ceiling parameter), the publication ORDERING
(asserted by a RECORDING FAKE, not by inspection), the three-registry
release ORDER, the exit-kind vocabulary, the single bytes→KB conversion,
the durable row staying OPEN, the bounded wait's derivation, and the
additive refusal / pending surfaces.

**Fencing (test-plan.md §2).**  Every spawning test runs under the house
fences: ``ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`` is pinned by the
runner, the ``POSTGRES_*`` family is scrubbed per-variable, the ceilings
are INJECTED and MiB-scale (the production default is NEVER used), and
the harness refuses to allocate when the host is short on memory.

**The goldens** below were captured from the BASE COMMIT ``b1c46e302``
(the RAM-guard substrate commit) — the byte-identity contract is
"identical to the base commit", so the reference is a checked-in golden
with its provenance recorded inline (test-plan.md §1.3).
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import time
from pathlib import Path

import pytest

bash_module = importlib.import_module("daemon.tools.bash")

from daemon.services.ram_guard import set_ram_guard
from daemon.services.ram_guard.errors import ENVELOPE_KEYS
from daemon.services.ram_guard.guard import RAM_GUARD_PENDING_MAX
from daemon.services.ram_guard.verdict import (
    STATE_BREACHED,
    STATE_KILLED,
    STATE_RELEASED,
    STATE_REFUSED,
    STATE_WATCHING,
    SpawnRecord,
)
from daemon.services.resource_monitor.pid_registry import (
    EXIT_KIND_RAM_KILLED,
    EXIT_KIND_SUCCESS,
    EXIT_KIND_TIMEOUT,
    VALID_EXIT_KINDS,
    get_pid_registry,
    reset_pid_registry_for_tests,
)

INSTANCE = "inst-ram-guard-tests"

#: The INJECTED ceiling — 64 MiB (never the production default).
CEILING_BYTES = 64 * 1024 * 1024
#: Each grandchild's private allocation.
CHILD_BYTES = 40 * 1024 * 1024
#: The grandchild count.
CHILD_COUNT = 3
#: The planned allocation (the harness's headroom check).
PLANNED_BYTES = CHILD_BYTES * CHILD_COUNT

# ── The base-commit goldens (test-plan.md §1.3) ────────────────────────────
# Provenance: base commit b1c46e302, worktree
# /home/nea/ensemble-src-wt-ram-auto-termination, captured with
#   bash.coroutine(command=<the command below>, instance_id="golden-inst")
GOLDEN_ECHO_HI = "STDOUT:\nhi\n\n\nEXIT CODE: 0"
GOLDEN_STDOUT_STDERR = "STDOUT:\nout\n\n\nSTDERR:\nerr\n\n\nEXIT CODE: 0"
GOLDEN_EXIT_3 = "STDOUT:\nx\n\n\nEXIT CODE: 3"

#: The ballooning script — REAL processes that genuinely allocate.
#: Each forked child touches every page of its own buffer, so the
#: private (PSS-visible) footprint is real and NOT a shared-page
#: over-count.  The pid file records every grandchild so the test can
#: assert that EVERY one of them died.
_BALLOON_PY = """
import os, sys, time
size = int(sys.argv[1])
kids = int(sys.argv[2])
pidfile = sys.argv[3]
for _ in range(kids):
    pid = os.fork()
    if pid == 0:
        try:
            fd = os.open(pidfile, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.write(fd, (str(os.getpid()) + "\\n").encode())
            os.close(fd)
            buf = bytearray(size)
            for off in range(0, size, 4096):
                buf[off] = 1
            time.sleep(120)
        finally:
            os._exit(0)
time.sleep(120)
"""


# ── The helpers ────────────────────────────────────────────────────────────


def _mem_available_bytes() -> int:
    """The host's available memory (0 when it cannot be read)."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return 0
    return 0


def _require_headroom(needed: int) -> None:
    """Refuse to allocate when the host is short on memory."""
    available = _mem_available_bytes()
    if available and available < needed:
        pytest.skip(
            f"insufficient host memory: {available} bytes available, "
            f"{needed} required"
        )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OSError):
        return False


def _read_pids(pidfile: Path) -> list[int]:
    try:
        raw = pidfile.read_text()
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if line.isdigit():
            out.append(int(line))
    return out


def _make_record(**overrides) -> SpawnRecord:
    """A real :class:`SpawnRecord` (the substrate's own dataclass)."""
    record = SpawnRecord(
        tracking_id="tracking-1",
        instance_id=INSTANCE,
        lane="bash",
        pid=4242,
        pgid=4242,
        start_time_jiffies=1,
        ceiling_bytes=CEILING_BYTES,
        poll_interval_ms=100,
        grace_ms=100,
    )
    for key, value in overrides.items():
        setattr(record, key, value)
    return record


class _RecordingGuard:
    """A RECORDING fake for the guard — the ORDERING assertions.

    The fake publishes the verdict on a chosen read (``publish_after_reads``)
    so the lane's ordering — publication BEFORE ``mark_exit`` and BEFORE
    the guard release — is OBSERVABLE, not assumed (phase3-plan.md T5).
    """

    def __init__(
        self,
        record: SpawnRecord | None = None,
        *,
        events: list | None = None,
        publish_after_reads: int | None = None,
        publish_reason: str = "killed:ladder",
    ) -> None:
        self.record = record
        self.events = events if events is not None else []
        self.publish_after_reads = publish_after_reads
        self.publish_reason = publish_reason
        self.registered: list[dict] = []
        self.released: list[str] = []
        self.release_calls: list[str] = []
        self.release_refusals: list[str] = []
        self._reads = 0

    # ── the guard's registry API (the facade calls exactly these three) ──
    def register(self, **kwargs):
        self.events.append(("register", kwargs))
        self.registered.append(kwargs)
        return self.record

    def read_verdict(self, tracking_id: str):
        self._reads += 1
        self.events.append(("read_verdict", self._reads))
        if (
            self.publish_after_reads is not None
            and self._reads >= self.publish_after_reads
            and self.record is not None
        ):
            # The execution-lane thread PUBLISHES the verdict.
            self.record.state = STATE_KILLED
            self.record.reason = self.publish_reason
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
        # The guard's OWN release rules (arch §15 B21): a CLAIMED
        # record's release is deferred to the lane; a LIVE watch (a
        # non-empty group) is the liveness sweep's to release.
        if self.record is not None and not force:
            if self.record.claim_token is not None:
                self.release_refusals.append(tracking_id)
                return False
            if self.record.group_member_count > 0:
                self.release_refusals.append(tracking_id)
                return False
        self.released.append(tracking_id)
        return True


class _FakeProcess:
    """A stand-in for ``asyncio.subprocess.Process``."""

    pid = 4242
    returncode = 0

    def wait(self):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        loop.call_soon(future.set_result, 0)
        return future


class _FakeBashRegistry:
    """A minimal ``BashProcessRegistry`` stand-in that records calls."""

    def __init__(self, events: list | None = None) -> None:
        self.events = events if events is not None else []
        self.registers: list[tuple] = []
        self.unregisters: list[tuple] = []

    def get_durable_writer(self):
        return None

    async def register(self, instance_id: str, pid: int, pgid: int) -> None:
        self.registers.append((instance_id, pid, pgid))

    async def unregister(self, instance_id: str, pid: int) -> None:
        self.unregisters.append((instance_id, pid))
        self.events.append("bash_registry_unregister")


@pytest.fixture(autouse=True)
def _switches_on(monkeypatch):
    """Both master switches default ON (absent ⇒ ON in every lane)."""
    monkeypatch.delenv("ENSEMBLE_RAM_GUARD", raising=False)
    monkeypatch.delenv("ENSEMBLE_BASH_ORPHAN_REAPER", raising=False)


@pytest.fixture(autouse=True)
def _ram_fence(monkeypatch, tmp_path):
    """T2 — every spawning test runs under the house fence; ASSERTED to hold."""
    from tests.helpers.ram_guard_fence import apply_fence, assert_fence_applied

    fake_home, auth_names = apply_fence(monkeypatch, tmp_path)
    yield
    assert_fence_applied(fake_home, auth_names)


@pytest.fixture(autouse=True)
def _clean_pid_registry():
    reset_pid_registry_for_tests()
    yield
    reset_pid_registry_for_tests()


@pytest.fixture
def fake_guard():
    """Install a recording fake guard; clear it afterwards."""
    guard = _RecordingGuard()
    set_ram_guard(guard)
    try:
        yield guard
    finally:
        set_ram_guard(None)


@pytest.fixture
def real_guard():
    """A REAL guard with an INJECTED 64 MiB ceiling (never the default)."""
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


def _balloon_script(tmp_path: Path) -> Path:
    script = tmp_path / "balloon.py"
    script.write_text(_BALLOON_PY)
    return script


# ── T1 — the SECURITY ROUTING: the schema gains NO ceiling parameter ──────


class TestSecurityRuling:
    def test_llm_facing_schema_has_no_ceiling_parameter(self):
        assert list(bash_module.BashInputSchema.model_fields) == [
            "command",
            "timeout",
            "workdir",
            "input",
            "env",
        ]
        assert "ram_ceiling_bytes" not in bash_module.bash.args_schema.model_fields
        assert "ceiling" not in json.dumps(
            bash_module.bash.args_schema.model_json_schema()
        )

    async def test_the_internal_ceiling_seam_is_honoured(self, fake_guard):
        """The INTERNAL override reaches the guard's registration."""
        await bash_module.bash.coroutine(
            command="echo hi",
            instance_id=INSTANCE,
            ram_ceiling_bytes=12345678,
        )
        assert len(fake_guard.registered) == 1
        assert fake_guard.registered[0]["ceiling_bytes"] == 12345678

    async def test_no_override_uses_the_guard_resolved_ceiling(self, fake_guard):
        await bash_module.bash.coroutine(command="echo hi", instance_id=INSTANCE)
        assert len(fake_guard.registered) == 1
        assert fake_guard.registered[0]["ceiling_bytes"] is None


# ── T2 — the kill-switch ───────────────────────────────────────────────────


class TestKillSwitch:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, True),
            ("", True),
            ("   ", True),
            ("0", False),
            ("false", False),
            ("FALSE", False),
            ("no", False),
            ("off", False),
            ("OFF", False),
            ("1", True),
            ("yes", True),
            ("enabled", True),
        ],
    )
    def test_switch_parsing(self, raw, expected):
        env = {} if raw is None else {"ENSEMBLE_RAM_GUARD": raw}
        assert bash_module._ram_guard_switch(env) is expected

    async def test_switch_off_registers_nothing(self, fake_guard, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_RAM_GUARD", "0")
        out = await bash_module.bash.coroutine(command="echo hi", instance_id=INSTANCE)
        assert out == GOLDEN_ECHO_HI
        assert fake_guard.registered == []

    async def test_switch_off_is_byte_identical_to_on(self, real_guard, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_RAM_GUARD", "0")
        off = await bash_module.bash.coroutine(
            command="echo hi", instance_id=INSTANCE
        )
        # No registration ⇒ no record ⇒ no measurement of this tree.
        assert real_guard.health()["registry_depth"] == 0
        monkeypatch.delenv("ENSEMBLE_RAM_GUARD", raising=False)
        on = await bash_module.bash.coroutine(command="echo hi", instance_id=INSTANCE)
        assert off == on == GOLDEN_ECHO_HI
        # The ON path DOES register (the switch is the only difference).
        assert real_guard.health()["registry_depth"] == 1

    async def test_no_guard_installed_is_a_no_op(self, monkeypatch):
        assert bash_module._get_ram_guard() is None
        out = await bash_module.bash.coroutine(command="echo hi", instance_id=INSTANCE)
        assert out == GOLDEN_ECHO_HI


# ── T3 — the registration (the captured pgid; the fallback invariant) ─────


class TestRegistration:
    async def test_registration_uses_the_captured_pgid_and_never_re_derives(
        self, fake_guard, monkeypatch
    ):
        real_getpgid = os.getpgid
        calls: list[int] = []

        def counting_getpgid(pid):
            calls.append(pid)
            return real_getpgid(pid)

        monkeypatch.setattr(bash_module.os, "getpgid", counting_getpgid)
        await bash_module.bash.coroutine(command="echo hi", instance_id=INSTANCE)
        # NO second getpgid — the TOCTOU the capture block respects.
        assert len(calls) == 1
        assert len(fake_guard.registered) == 1
        registration = fake_guard.registered[0]
        assert registration["pgid"] == registration["pid"]
        assert registration["lane"] == "bash"
        assert registration["instance_id"] == INSTANCE
        assert registration["tracking_id"]

    async def test_pgid_falls_back_to_pid_when_getpgid_raises(
        self, fake_guard, monkeypatch
    ):
        def raising_getpgid(_pid):
            raise ProcessLookupError("no such process")

        monkeypatch.setattr(bash_module.os, "getpgid", raising_getpgid)
        await bash_module.bash.coroutine(command="echo hi", instance_id=INSTANCE)
        assert len(fake_guard.registered) == 1
        registration = fake_guard.registered[0]
        # start_new_session=True ⇒ PGID == PID.
        assert registration["pgid"] == registration["pid"]

    async def test_no_instance_id_registers_nothing(self, fake_guard, caplog):
        with caplog.at_level("WARNING"):
            out = await bash_module.bash.coroutine(command="echo hi", instance_id=None)
        assert "hi" in out
        assert fake_guard.registered == []


# ── T5 — the PUBLICATION ORDERING (the recording fake) ─────────────────────


class TestPublicationOrdering:
    async def test_publication_precedes_mark_exit_and_the_guard_release(
        self, monkeypatch
    ):
        events: list = []
        record = _make_record(
            state=STATE_BREACHED,
            breach_at=time.time(),
            claim_token=11,
            observed_peak_bytes=96 * 1024 * 1024,
        )
        guard = _RecordingGuard(record, events=events, publish_after_reads=2)
        set_ram_guard(guard)
        try:
            monkeypatch.setattr(
                bash_module,
                "_pid_registry_mark_exit",
                lambda **kwargs: events.append(("mark_exit", kwargs["exit_kind"])),
            )
            out = await bash_module.bash.coroutine(
                command="echo hi", instance_id=INSTANCE
            )
        finally:
            set_ram_guard(None)

        payload = json.loads(out)
        assert payload["code"] == "ram_limit_exceeded"

        kinds = [event[0] for event in events]
        publication = kinds.index("publication")
        release = kinds.index("release")
        mark_exit = kinds.index("mark_exit")
        # The publication is observed BEFORE the release and BEFORE the
        # mark_exit — the ordering is observable, not assumed.
        assert publication < release
        assert publication < mark_exit
        assert release < mark_exit
        # The success arm's mark_exit did NOT run.
        exit_kinds = [event[1] for event in events if event[0] == "mark_exit"]
        assert exit_kinds == [EXIT_KIND_RAM_KILLED]
        assert EXIT_KIND_SUCCESS not in exit_kinds

    async def test_the_success_arm_mark_exit_does_not_run_on_a_ram_kill(
        self, monkeypatch
    ):
        events: list = []
        record = _make_record(
            state=STATE_KILLED,
            reason="killed:ladder",
            breach_at=time.time(),
            killed_at=time.time(),
            observed_peak_bytes=96 * 1024 * 1024,
        )
        guard = _RecordingGuard(record, events=events)
        set_ram_guard(guard)
        try:
            monkeypatch.setattr(
                bash_module,
                "_pid_registry_mark_exit",
                lambda **kwargs: events.append(("mark_exit", kwargs["exit_kind"])),
            )
            await bash_module.bash.coroutine(
                command="echo hi", instance_id=INSTANCE
            )
        finally:
            set_ram_guard(None)
        exit_kinds = [event[1] for event in events if event[0] == "mark_exit"]
        assert exit_kinds == [EXIT_KIND_RAM_KILLED]


class TestInvertedOrderingErasesTheVerdict:
    """D-1 (phase6 T13 acceptance) — the ERASE DEMO.

    ``PidRegistry.mark_exit`` is ONE-SHOT: it deletes the live row on the
    FIRST exit_kind (``pid_registry.py:423``).  A second ``mark_exit`` for
    the same ``(pid, start_time)`` is a NO-OP (the row is gone).  So if the
    success arm's ``mark_exit(EXIT_KIND_SUCCESS)`` ran BEFORE the RAM arm,
    the RAM ``mark_exit(EXIT_KIND_RAM_KILLED)`` would find nothing and
    PERMANENTLY erase the RAM verdict.

    This test DRIVES that outcome directly against the real registry — the
    counter-mutation (matrix leg M14) plants the inverted ordering in
    ``bash.py`` and reddens the positive-ordering pin.
    """

    def test_an_inverted_ordering_permanently_erases_the_ram_verdict(self):
        reset_pid_registry_for_tests()
        registry = get_pid_registry()
        pid, start_time = 700001, 12345
        registry.register(
            pid=pid,
            start_time=start_time,
            kind="bash",
            instance_id=INSTANCE,
            pgid=pid,
            command="echo hi",
            detail="erase-demo",
            parent_pid=os.getpid(),
        )
        # INVERTED ordering: the success arm's mark_exit runs FIRST.
        registry.mark_exit(
            pid=pid,
            start_time=start_time,
            exit_code=0,
            exit_kind=EXIT_KIND_SUCCESS,
            peak_rss_kb=1,
        )
        # …then the RAM arm's mark_exit runs — and finds NOTHING (the live
        # row was already deleted): the verdict is ERASED.
        erased = registry.mark_exit(
            pid=pid,
            start_time=start_time,
            exit_code=None,
            exit_kind=EXIT_KIND_RAM_KILLED,
            peak_rss_kb=96 * 1024,
        )
        assert erased is None  # the one-shot delete already consumed the row
        history = registry.drain_history()
        exit_kinds = [row.exit_kind for row in history]
        # The RAM verdict is ABSENT from the record — permanently erased.
        assert EXIT_KIND_RAM_KILLED not in exit_kinds
        assert exit_kinds == [EXIT_KIND_SUCCESS]

    def test_the_correct_ordering_preserves_the_ram_verdict(self):
        reset_pid_registry_for_tests()
        registry = get_pid_registry()
        pid, start_time = 700002, 22222
        registry.register(
            pid=pid,
            start_time=start_time,
            kind="bash",
            instance_id=INSTANCE,
            pgid=pid,
            command="echo hi",
            detail="preserve-demo",
            parent_pid=os.getpid(),
        )
        # CORRECT ordering: the RAM arm's mark_exit runs (only) — the verdict
        # SURVIVES unambiguously.
        kept = registry.mark_exit(
            pid=pid,
            start_time=start_time,
            exit_code=None,
            exit_kind=EXIT_KIND_RAM_KILLED,
            peak_rss_kb=96 * 1024,
        )
        assert kept is not None
        history = registry.drain_history()
        assert [row.exit_kind for row in history] == [EXIT_KIND_RAM_KILLED]


# ── T12 — the three-registry release ORDER ─────────────────────────────────


class TestReleaseOrder:
    async def _run_ram_arm(self, monkeypatch, order, *, raise_guard=False):
        record = _make_record(
            state=STATE_KILLED,
            reason="killed:ladder",
            breach_at=time.time(),
            killed_at=time.time(),
            observed_peak_bytes=96 * 1024 * 1024,
        )
        guard = _RecordingGuard(record)
        set_ram_guard(guard)
        try:
            def guard_release(_tracking_id, **_kwargs):
                order.append("guard")
                if raise_guard:
                    raise RuntimeError("the guard release exploded")
                return True

            monkeypatch.setattr(bash_module, "_ram_guard_release", guard_release)
            monkeypatch.setattr(
                bash_module,
                "get_bash_process_registry",
                lambda: _FakeBashRegistry(order),
            )
            monkeypatch.setattr(
                bash_module,
                "_pid_registry_mark_exit",
                lambda **kwargs: order.append("mark_exit"),
            )
            out = await bash_module.bash.coroutine(
                command="echo hi", instance_id=INSTANCE
            )
        finally:
            set_ram_guard(None)
        return out

    async def test_release_order_is_guard_then_registry_then_pid_registry(
        self, monkeypatch
    ):
        order: list = []
        await self._run_ram_arm(monkeypatch, order)
        # The three release steps, IN ORDER: guard registry → the
        # BashProcessRegistry → the pid_registry's mark_exit.
        assert "guard" in order
        assert "bash_registry_unregister" in order
        assert "mark_exit" in order
        assert (
            order.index("guard")
            < order.index("bash_registry_unregister")
            < order.index("mark_exit")
        )

    async def test_a_raising_release_step_does_not_abort_the_others(
        self, monkeypatch
    ):
        order: list = []
        await self._run_ram_arm(monkeypatch, order, raise_guard=True)
        # The guard release raised — the other two STILL ran.
        assert "bash_registry_unregister" in order
        assert "mark_exit" in order
        assert (
            order.index("guard")
            < order.index("bash_registry_unregister")
            < order.index("mark_exit")
        )


# ── T6 — the typed envelope ────────────────────────────────────────────────


class TestEnvelope:
    async def test_the_envelope_key_set_is_exact_and_ordered(self, monkeypatch):
        record = _make_record(
            state=STATE_KILLED,
            reason="killed:ladder",
            breach_at=time.time(),
            killed_at=time.time(),
            observed_peak_bytes=96 * 1024 * 1024,
            confirmed_pss_bytes=100 * 1024 * 1024,
        )
        guard = _RecordingGuard(record)
        set_ram_guard(guard)
        try:
            out = await bash_module.bash.coroutine(
                command="echo hi", instance_id=INSTANCE
            )
        finally:
            set_ram_guard(None)
        payload = json.loads(out)
        assert list(payload.keys()) == list(ENVELOPE_KEYS)
        assert payload["ok"] is False
        assert payload["code"] == "ram_limit_exceeded"
        assert payload["surface"] == "bash.exec"
        assert payload["reason"].startswith("killed:")
        assert payload["limit_bytes"] == CEILING_BYTES
        # ``rearm_threshold_bytes`` sits IMMEDIATELY after ``limit_bytes``.
        keys = list(payload.keys())
        assert keys.index("rearm_threshold_bytes") == keys.index("limit_bytes") + 1
        assert payload["rearm_threshold_bytes"] == 0
        assert payload["ram_breach_observed"] is True
        assert payload["coincident_timeout"] is False
        # The DECIDING value clears the ceiling.
        assert payload["confirmed_pss_bytes"] >= payload["limit_bytes"]
        # The message carries the enforced-threshold clause, verbatim.
        assert payload["message"].startswith(
            "The enforced trigger threshold was "
        )
        assert "the resolved ceiling is" in payload["message"]

    async def test_the_envelope_is_the_entire_return_string(self, monkeypatch):
        record = _make_record(
            state=STATE_KILLED,
            reason="killed:ladder",
            breach_at=time.time(),
            killed_at=time.time(),
        )
        guard = _RecordingGuard(record)
        set_ram_guard(guard)
        try:
            out = await bash_module.bash.coroutine(
                command="echo hi", instance_id=INSTANCE
            )
        finally:
            set_ram_guard(None)
        # It PARSES as JSON — nothing wraps it.
        json.loads(out)
        assert "EXIT CODE" not in out

    async def test_the_envelope_preempts_the_truncation_arm(self, monkeypatch):
        """D7 — a >150 000-byte output does NOT truncate a RAM kill.

        The phase-3 risk row makes a truncation-shaped return for a RAM
        kill a test failure: the RAM arm returns the envelope BEFORE
        the 150 k truncation arm.
        """
        record = _make_record(
            state=STATE_KILLED,
            reason="killed:ladder",
            breach_at=time.time(),
            killed_at=time.time(),
            observed_peak_bytes=96 * 1024 * 1024,
        )
        guard = _RecordingGuard(record)
        set_ram_guard(guard)
        try:
            out = await bash_module.bash.coroutine(
                command=[
                    "python3",
                    "-c",
                    "import sys; sys.stdout.write('x' * 200000)",
                ],
                instance_id=INSTANCE,
            )
        finally:
            set_ram_guard(None)
        payload = json.loads(out)
        assert payload["code"] == "ram_limit_exceeded"
        # NOT a truncation-shaped return.
        assert "OUTPUT TRUNCATED" not in out
        assert "STDOUT:" not in out


# ── T8 — the additive surfaces (the refusal + the pending breach) ─────────


class TestAdditiveSurfaces:
    async def test_a_refusal_returns_the_ordinary_output_plus_the_lines(
        self, monkeypatch
    ):
        record = _make_record(
            state=STATE_REFUSED,
            reason="refused:pgid-recycled",
            breach_at=time.time(),
        )
        guard = _RecordingGuard(record)
        set_ram_guard(guard)
        try:
            out = await bash_module.bash.coroutine(
                command="echo hi", instance_id=INSTANCE
            )
        finally:
            set_ram_guard(None)
        # The output is NEVER swallowed.
        assert out.startswith(GOLDEN_ECHO_HI)
        assert "ram_kill_refused: refused:pgid-recycled" in out
        assert "ram_breach_observed: true" in out
        # The surface is distinct from the envelope by construction.
        assert "ram_limit_exceeded" not in out

    async def test_the_wait_expiry_reports_pending_not_fabricated(
        self, monkeypatch
    ):
        """A breach whose episode never resolves ⇒ the ORDINARY return."""
        record = _make_record(
            state=STATE_BREACHED,
            breach_at=time.time(),
            observed_peak_bytes=70 * 1024 * 1024,
            claim_token=3,
        )
        guard = _RecordingGuard(record)
        set_ram_guard(guard)
        try:
            out = await bash_module.bash.coroutine(
                command="echo hi", instance_id=INSTANCE
            )
        finally:
            set_ram_guard(None)
        assert out.startswith(GOLDEN_ECHO_HI)
        assert "ram_breach_observed: true" in out
        assert "ram_observed_peak_bytes: 73400320" in out
        # A pending decision is NEVER fabricated as a kill.
        assert "ram_limit_exceeded" not in out


# ── T15 — the bounded wait ─────────────────────────────────────────────────


class TestBoundedWait:
    def test_the_deadline_derivation_is_pinned(self):
        assert RAM_GUARD_PENDING_MAX == 2
        assert bash_module.RAM_GUARD_WAIT_SLACK_MS == 250
        assert bash_module.RAM_GUARD_PSS_CONFIRM_DEADLINE_MS == 250
        assert bash_module.RAM_GUARD_GRACE_MS == 250
        assert bash_module.RAM_GUARD_PROBE_MS == 10
        service_worst = (
            bash_module.RAM_GUARD_PSS_CONFIRM_DEADLINE_MS
            + bash_module.RAM_GUARD_GRACE_MS
            + bash_module.RAM_GUARD_PROBE_MS
        )
        assert service_worst == 510
        assert (
            bash_module.RAM_GUARD_WAIT_DEADLINE_MS
            == RAM_GUARD_PENDING_MAX * service_worst
            + bash_module.RAM_GUARD_WAIT_SLACK_MS
        )
        assert bash_module.RAM_GUARD_WAIT_DEADLINE_MS == 1270
        assert bash_module.RAM_GUARD_WAIT_POLL_SECONDS == 0.025

    async def test_the_wait_is_bounded_and_never_blocks_the_loop(self, monkeypatch):
        record = _make_record(
            state=STATE_BREACHED,
            breach_at=time.time(),
            claim_token=3,
        )
        guard = _RecordingGuard(record)
        set_ram_guard(guard)
        progress = {"ticks": 0}

        async def ticker():
            while True:
                progress["ticks"] += 1
                await asyncio.sleep(0.01)

        task = asyncio.create_task(ticker())
        try:
            started = time.monotonic()
            out = await bash_module.bash.coroutine(
                command="echo hi", instance_id=INSTANCE
            )
            elapsed_ms = (time.monotonic() - started) * 1000.0
        finally:
            task.cancel()
            set_ram_guard(None)
        # Bounded at the deadline (plus the command's own runtime).
        assert 1000.0 <= elapsed_ms <= 4000.0
        # The event loop was NOT blocked — the ticker progressed.
        assert progress["ticks"] > 10
        assert "ram_breach_observed: true" in out

    async def test_a_resolved_episode_does_not_burn_the_deadline(self, monkeypatch):
        record = _make_record(
            state=STATE_KILLED,
            reason="killed:ladder",
            breach_at=time.time(),
            killed_at=time.time(),
        )
        guard = _RecordingGuard(record)
        set_ram_guard(guard)
        try:
            started = time.monotonic()
            await bash_module.bash.coroutine(command="echo hi", instance_id=INSTANCE)
            elapsed_ms = (time.monotonic() - started) * 1000.0
        finally:
            set_ram_guard(None)
        assert elapsed_ms < 500.0

    def test_the_guard_exposes_the_public_wait_inputs(self):
        """D2 — the SEAM PIN: the PUBLIC read-only accessors exist.

        The lane derives its bounded wait from the guard's own
        service-worst-case inputs.  They are read through PUBLIC
        accessors (never the private attributes), so a substrate rename
        is a RED test here instead of a silent revert to the default
        deadline.
        """
        from daemon.services.ram_guard.guard import RamGuard

        assert hasattr(RamGuard, "grace_seconds")
        assert hasattr(RamGuard, "pss_confirm_deadline_ms")
        guard = RamGuard(
            enabled=True,
            resolved_ceiling=(CEILING_BYTES, "test"),
            grace_ms=500,
            pss_confirm_deadline_ms=400,
        )
        assert guard.grace_seconds == 0.5
        assert guard.pss_confirm_deadline_ms == 400

    def test_the_deadline_widens_for_a_non_default_guard(self):
        """D2 — the deadline covers the guard's OWN inputs."""
        from daemon.services.ram_guard.guard import RamGuard

        guard = RamGuard(
            enabled=True,
            resolved_ceiling=(CEILING_BYTES, "test"),
            grace_ms=500,
            pss_confirm_deadline_ms=400,
        )
        deadline = bash_module._ram_wait_deadline_ms(guard)
        assert deadline == 2 * (400 + 500 + 10) + 250
        assert deadline > bash_module.RAM_GUARD_WAIT_DEADLINE_MS

    def test_the_deadline_falls_back_to_the_documented_defaults(self):
        """D2 — a guard WITHOUT the public accessors ⇒ the pinned default."""
        class _BareGuard:
            pass

        assert (
            bash_module._ram_wait_deadline_ms(_BareGuard())
            == bash_module.RAM_GUARD_WAIT_DEADLINE_MS
            == 1270
        )


# ── T11 — the coincidence (the claim token) ────────────────────────────────


class TestCoincidence:
    async def test_the_timeout_wins_the_coincidence(self, monkeypatch):
        """The timeout owns the verdict ⇒ code="timeout" + the breach."""
        record = _make_record(
            state=STATE_RELEASED,
            reason="already-dead:group-empty",
            breach_at=time.time(),
            observed_peak_bytes=80 * 1024 * 1024,
        )
        guard = _RecordingGuard(record)
        set_ram_guard(guard)
        try:
            out = await bash_module.bash.coroutine(
                command="sleep 5", timeout=0.2, instance_id=INSTANCE
            )
        finally:
            set_ram_guard(None)
        payload = json.loads(out)
        assert payload["code"] == "timeout"
        assert payload["ram_breach_observed"] is True
        assert payload["coincident_timeout"] is True
        assert payload["timeout_seconds"] == 0.2

    async def test_the_guard_wins_the_coincidence(self, monkeypatch):
        record = _make_record(
            state=STATE_KILLED,
            reason="killed:ladder",
            breach_at=time.time(),
            killed_at=time.time(),
            observed_peak_bytes=96 * 1024 * 1024,
        )
        guard = _RecordingGuard(record)
        set_ram_guard(guard)
        try:
            out = await bash_module.bash.coroutine(
                command="sleep 5", timeout=0.2, instance_id=INSTANCE
            )
        finally:
            set_ram_guard(None)
        payload = json.loads(out)
        assert payload["code"] == "ram_limit_exceeded"
        assert payload["coincident_timeout"] is True
        assert payload["ram_breach_observed"] is True

    async def test_the_claim_token_deferral_skips_the_timeout_kill(
        self, monkeypatch
    ):
        """D1/T11/O9 — the LOSER skips its signal.

        The record carries a HELD claim token (the guard owns the
        episode) and the command times out ⇒ the timeout arm must NOT
        signal — the guard's signal is the only one — and the envelope
        still carries ``code="ram_limit_exceeded"`` +
        ``coincident_timeout=True``.
        """
        from unittest.mock import AsyncMock

        from tests.tools.conftest import _register_pid

        record = _make_record(
            state=STATE_KILLED,
            reason="killed:ladder",
            breach_at=time.time(),
            killed_at=time.time(),
            observed_peak_bytes=96 * 1024 * 1024,
            claim_token=7,  # the guard's episode is CLAIMED
        )
        guard = _RecordingGuard(record)
        set_ram_guard(guard)
        kill = AsyncMock()
        monkeypatch.setattr(bash_module, "_kill_process", kill)
        try:
            out = await bash_module.bash.coroutine(
                command="sleep 1", timeout=0.2, instance_id=INSTANCE
            )
        finally:
            set_ram_guard(None)
        # The timeout arm was the LOSER — it did NOT signal.
        kill.assert_not_awaited()
        # …and the verdict belongs to the guard.
        payload = json.loads(out)
        assert payload["code"] == "ram_limit_exceeded"
        assert payload["coincident_timeout"] is True
        assert payload["ram_breach_observed"] is True
        # The spawned child is registered for teardown (the timeout arm
        # deliberately did not kill it).
        if guard.registered:
            _register_pid(guard.registered[0]["pid"], "bash", "ram-guard-deferral")

    async def test_a_plain_timeout_stays_byte_identical(self, monkeypatch):
        """No breach ⇒ the legacy timeout string is untouched."""
        guard = _RecordingGuard(None)
        set_ram_guard(guard)
        try:
            out = await bash_module.bash.coroutine(
                command="sleep 5", timeout=0.2, instance_id=INSTANCE
            )
        finally:
            set_ram_guard(None)
        assert out.endswith("ERROR: Command timed out after 0.2 seconds")
        assert "ram_breach_observed" not in out


# ── T4 / T9 — no false attribution; the under-limit byte-identity ──────────


class TestNoFalseAttribution:
    async def test_a_signal_death_not_caused_by_the_guard_is_not_ram(
        self, monkeypatch
    ):
        """A record with no breach ⇒ the ordinary failure path."""
        record = _make_record(state=STATE_WATCHING, breach_at=None, reason=None)
        guard = _RecordingGuard(record)
        set_ram_guard(guard)
        try:
            out = await bash_module.bash.coroutine(
                command="echo hi", instance_id=INSTANCE
            )
        finally:
            set_ram_guard(None)
        assert out == GOLDEN_ECHO_HI
        assert "ram_limit_exceeded" not in out

    async def test_the_under_limit_output_is_byte_identical_to_the_base_commit(
        self, real_guard
    ):
        out = await bash_module.bash.coroutine(command="echo hi", instance_id=INSTANCE)
        assert out == GOLDEN_ECHO_HI

    async def test_the_stderr_shape_is_byte_identical_to_the_base_commit(
        self, real_guard
    ):
        out = await bash_module.bash.coroutine(
            command="echo out; echo err 1>&2", instance_id=INSTANCE
        )
        assert out == GOLDEN_STDOUT_STDERR

    async def test_the_exit_code_shape_is_byte_identical_to_the_base_commit(
        self, real_guard
    ):
        out = await bash_module.bash.coroutine(
            command="echo x; exit 3", instance_id=INSTANCE
        )
        assert out == GOLDEN_EXIT_3

    async def test_the_under_limit_exit_kind_is_still_success(self, real_guard):
        await bash_module.bash.coroutine(command="echo hi", instance_id=INSTANCE)
        history = get_pid_registry().drain_history()
        kinds = {entry.exit_kind for entry in history}
        assert EXIT_KIND_SUCCESS in kinds
        assert EXIT_KIND_RAM_KILLED not in kinds

    async def test_the_success_path_keeps_the_spawn_time_floor_semantics(
        self, real_guard, monkeypatch
    ):
        """T9 — the success path's peak is the SPAWN-TIME floor.

        The RAM arm is the ONLY path that reports a sampled peak; the
        success path keeps its documented under-report (the spawn-time
        VmHWM floor), byte-identical to the base commit.
        """
        calls = {"n": 0}
        real_read = bash_module._read_proc_vm_hwm_kb

        def fake_read(pid):
            calls["n"] += 1
            if calls["n"] == 1:
                # The SPAWN-TIME read.
                return 4242
            # The post-wait read misses (the proc is reaped).
            return None

        monkeypatch.setattr(bash_module, "_read_proc_vm_hwm_kb", fake_read)
        await bash_module.bash.coroutine(command="echo hi", instance_id=INSTANCE)
        history = get_pid_registry().drain_history()
        rows = [row for row in history if row.exit_kind == EXIT_KIND_SUCCESS]
        assert len(rows) == 1
        # The spawn-time floor — NOT the guard's sampled peak.
        assert rows[0].peak_rss_kb == 4242

    async def test_the_ram_arm_adds_no_child_env_stamp(self, real_guard, monkeypatch):
        """T2 — the RAM arm writes NO env var into the child."""
        from daemon.services.bash_spawn_reaper import BASH_TRACKING_ENV_VAR

        captured = {}

        real_shell = asyncio.create_subprocess_shell

        async def spy(*args, **kwargs):
            captured["env"] = kwargs.get("env")
            return await real_shell(*args, **kwargs)

        monkeypatch.setattr(bash_module.asyncio, "create_subprocess_shell", spy)
        await bash_module.bash.coroutine(command="echo hi", instance_id=INSTANCE)
        env = captured["env"]
        assert isinstance(env, dict)
        # Exactly the inherited env plus the reaper's OWN stamp — the RAM
        # arm contributes nothing.
        assert set(env.keys()) == set(os.environ.keys()) | {BASH_TRACKING_ENV_VAR}


# ── T13 — the exit-kind vocabulary ─────────────────────────────────────────


class TestExitKindVocabulary:
    def test_ram_killed_is_a_member_of_the_valid_exit_kinds(self):
        assert "ram_killed" in VALID_EXIT_KINDS
        assert EXIT_KIND_RAM_KILLED == "ram_killed"

    def test_the_exit_kind_set_is_exact(self):
        assert VALID_EXIT_KINDS == frozenset(
            {
                "success",
                "timeout",
                "cancelled",
                "exit",
                "unknown",
                "ram_killed",
            }
        )


# ── T7 — the durable row stays OPEN ────────────────────────────────────────


class TestDurableRowStaysOpen:
    async def test_the_durable_row_is_not_closed_by_a_ram_kill(
        self, monkeypatch, tmp_path
    ):
        from sqlalchemy import event as sa_event
        from sqlalchemy.pool import StaticPool
        from sqlmodel import SQLModel, create_engine

        from daemon.repositories.bash_spawn import BashSpawnRepository

        engine = create_engine(
            f"sqlite:///{tmp_path / 'ram-durable.sqlite'}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @sa_event.listens_for(engine, "connect")
        def _pragma(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA busy_timeout=10000")
            cur.close()

        SQLModel.metadata.create_all(engine)
        repo = BashSpawnRepository(engine=engine)
        record = _make_record(
            state=STATE_KILLED,
            reason="killed:ladder",
            breach_at=time.time(),
            killed_at=time.time(),
        )
        guard = _RecordingGuard(record)
        set_ram_guard(guard)
        try:
            from daemon.tools.bash import BashProcessRegistry

            registry = BashProcessRegistry()
            registry.set_durable_writer(repo)
            monkeypatch.setattr(
                bash_module, "get_bash_process_registry", lambda: registry
            )
            out = await bash_module.bash.coroutine(
                command="echo hi", instance_id=INSTANCE
            )
        finally:
            set_ram_guard(None)
        assert json.loads(out)["code"] == "ram_limit_exceeded"
        # The row is still OPEN — closing it would disarm the reaper's
        # escapee net (the sweep only sweeps open rows).
        rows = repo.list_open_by_instance(INSTANCE)
        assert len(rows) == 1
        assert rows[0].reaped_at is None
        engine.dispose()


# ── T14 (d) — the negative paths ───────────────────────────────────────────


class TestNegativePaths:
    async def test_a_non_linux_platform_guard_refuses_and_never_kills(
        self, monkeypatch, tmp_path
    ):
        """The guard is INERT off Linux — no kill, no false attribution."""
        from daemon.services.ram_guard.guard import RamGuard

        guard = RamGuard(
            enabled=True,
            resolved_ceiling=(CEILING_BYTES, "test"),
            poll_interval_ms=100,
            grace_ms=100,
            platform="win32",
        )
        set_ram_guard(guard)
        guard.start()
        script = tmp_path / "alloc.py"
        script.write_text(
            "import sys, time\n"
            "buf = bytearray(96 * 1024 * 1024)\n"
            "for off in range(0, len(buf), 4096):\n"
            "    buf[off] = 1\n"
            "time.sleep(1.5)\n"
            "print('done')\n"
        )
        try:
            out = await bash_module.bash.coroutine(
                command=["python3", str(script)], instance_id=INSTANCE
            )
        finally:
            guard.stop()
            guard.join(3.0)
            set_ram_guard(None)
        # The command ran to completion — the guard never killed it.
        assert "done" in out
        # The refusal surface — the reason is reported, not swallowed.
        assert "ram_kill_refused: refused:platform-unsupported" in out
        assert "ram_breach_observed: true" in out
        assert "ram_limit_exceeded" not in out

    async def test_an_unreadable_proc_root_produces_no_false_breach(
        self, monkeypatch, tmp_path
    ):
        """An ENUMERATION FAILURE drops the sample — never a kill."""
        from daemon.services.ram_guard.guard import RamGuard

        guard = RamGuard(
            enabled=True,
            resolved_ceiling=(CEILING_BYTES, "test"),
            poll_interval_ms=50,
            grace_ms=50,
            proc_root=Path("/nonexistent-proc-root-for-tests"),
        )
        set_ram_guard(guard)
        guard.start()
        try:
            out = await bash_module.bash.coroutine(
                command="echo hi", instance_id=INSTANCE
            )
        finally:
            guard.stop()
            guard.join(3.0)
            set_ram_guard(None)
        assert out == GOLDEN_ECHO_HI
        assert "ram_limit_exceeded" not in out

    async def test_an_enumeration_failure_is_not_a_breach(self, monkeypatch):
        """A record whose sample was dropped ⇒ no breach ⇒ no attribution."""
        record = _make_record(
            state=STATE_WATCHING,
            breach_at=None,
            reason=None,
            enumeration_failures=3,
        )
        guard = _RecordingGuard(record)
        set_ram_guard(guard)
        try:
            out = await bash_module.bash.coroutine(
                command="echo hi", instance_id=INSTANCE
            )
        finally:
            set_ram_guard(None)
        assert out == GOLDEN_ECHO_HI


# ── T16 — the ORDERING obligation (no subreaper; the reap is not raced) ────


class TestOrderingObligation:
    def test_no_subreaper_is_introduced_anywhere_in_the_daemon(self):
        """T16 — the guard kills a GROUP; init reaps the grandchildren.

        The daemon is NOT the reaper of a killed tree's grandchildren:
        there is no ``PR_SET_CHILD_SUBREAPER``, no ``SIGCHLD`` handler
        and no second ``waitpid`` anywhere in ``daemon/`` (the only
        ``waitpid`` is the dedicated upgrade-journal reaper).  A
        subreaper would change the daemon's own signal/reap semantics
        — the lane does NOT introduce one.
        """
        repo_root = Path(__file__).resolve().parents[2]
        daemon_root = repo_root / "daemon"
        offenders: list[str] = []
        for py in sorted(daemon_root.rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            text = py.read_text(encoding="utf-8", errors="replace")
            if "PR_SET_CHILD_SUBREAPER" in text:
                offenders.append(f"{py.relative_to(repo_root)}: PR_SET_CHILD_SUBREAPER")
            if "signal.SIGCHLD" in text:
                offenders.append(f"{py.relative_to(repo_root)}: signal.SIGCHLD")
        assert offenders == []

    @pytest.mark.timeout(90)
    async def test_the_direct_child_is_reaped_by_the_existing_await(
        self, real_guard, tmp_path
    ):
        """T16 — the direct child's exit is observed (the lane never forks).

        The guard kills a GROUP; the direct child is reaped by the
        pre-existing ``await proc.wait()`` — the lane adds no reaping
        of its own and never forks.
        """
        _require_headroom(PLANNED_BYTES * 4)
        script = _balloon_script(tmp_path)
        pidfile = tmp_path / "pids.txt"
        out = await bash_module.bash.coroutine(
            command=[
                "python3",
                str(script),
                str(CHILD_BYTES),
                str(CHILD_COUNT),
                str(pidfile),
            ],
            instance_id=INSTANCE,
        )
        payload = json.loads(out)
        # The call RETURNED ⇒ the await observed the direct child's exit.
        assert payload["pid"] > 0
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and _pid_alive(payload["pid"]):
            await asyncio.sleep(0.05)
        assert not _pid_alive(payload["pid"])


# ── T8 — the SIX exit paths each request the CONDITIONAL release ──────────


class TestSixExitPaths:
    """Every bash exit path asks the guard to release — CONDITIONALLY.

    The release is conditional: it never ends a live watch (a record
    whose group is still live is refused by the guard) and a CLAIMED
    record's release is deferred to the lane.  The lane's finally-shaped
    release covers all six paths (success, timeout, RAM, cancel,
    generic-exception, spawn-failure).
    """

    async def test_the_cancellation_arm_requests_the_release(
        self, fake_guard, monkeypatch
    ):
        from unittest.mock import AsyncMock

        fake_guard.record = _make_record(samples=1)
        proc = _FakeProcess()
        monkeypatch.setattr(
            bash_module.asyncio,
            "create_subprocess_shell",
            AsyncMock(return_value=proc),
        )
        monkeypatch.setattr(
            bash_module.asyncio,
            "wait_for",
            AsyncMock(side_effect=asyncio.CancelledError),
        )
        monkeypatch.setattr(bash_module, "_kill_process", AsyncMock())
        monkeypatch.setattr(
            bash_module, "get_bash_process_registry", lambda: _FakeBashRegistry()
        )
        monkeypatch.setattr(bash_module.os, "getpgid", lambda _pid: proc.pid)
        with pytest.raises(asyncio.CancelledError):
            await bash_module.bash.coroutine(
                command="echo hi", instance_id=INSTANCE
            )
        assert fake_guard.release_calls

    async def test_the_spawn_failure_arm_requests_the_release(
        self, fake_guard, monkeypatch
    ):
        """D6 — the SPAWN-FAILURE shape (the spawn itself raised)."""
        from unittest.mock import AsyncMock

        fake_guard.record = _make_record(samples=1)
        monkeypatch.setattr(
            bash_module.asyncio,
            "create_subprocess_shell",
            AsyncMock(side_effect=RuntimeError("the spawn exploded")),
        )
        monkeypatch.setattr(
            bash_module, "get_bash_process_registry", lambda: _FakeBashRegistry()
        )
        out = await bash_module.bash.coroutine(
            command="echo hi", instance_id=INSTANCE
        )
        assert out.startswith("ERROR:")
        assert fake_guard.release_calls

    async def test_the_mid_lane_generic_exception_arm_requests_the_release(
        self, fake_guard, monkeypatch
    ):
        """D6 — a genuine MID-LANE failure (the spawn SUCCEEDED)."""
        from unittest.mock import AsyncMock

        fake_guard.record = _make_record(samples=1)
        # The lane's own verdict observation raises — after the spawn,
        # after the await.  The generic arm catches it.
        monkeypatch.setattr(
            bash_module,
            "_await_ram_verdict",
            AsyncMock(side_effect=RuntimeError("the lane exploded")),
        )
        out = await bash_module.bash.coroutine(
            command="echo hi", instance_id=INSTANCE
        )
        assert out.startswith("ERROR:")
        assert "the lane exploded" in out
        # The generic arm has its OWN release (phase3-plan.md T8's
        # letter) AND the finally-shaped backstop runs behind it — two
        # requests on this path, one on the ordinary paths.
        assert len(fake_guard.release_calls) == 2

    async def test_the_success_path_requests_the_release(self, fake_guard, monkeypatch):
        """D10 — the SUCCESS path's finally-shaped release runs."""
        fake_guard.record = _make_record(samples=1)
        await bash_module.bash.coroutine(command="echo hi", instance_id=INSTANCE)
        # The finally-shaped release only (the success path has no arm
        # of its own).
        assert len(fake_guard.release_calls) == 1

    async def test_the_timeout_path_requests_the_release(self, fake_guard, monkeypatch):
        """D10 — the TIMEOUT path's finally-shaped release runs."""
        fake_guard.record = _make_record(samples=1)
        await bash_module.bash.coroutine(
            command="sleep 1", timeout=0.2, instance_id=INSTANCE
        )
        assert fake_guard.release_calls

    async def test_the_ram_path_requests_the_release(self, fake_guard, monkeypatch):
        """D10 — the RAM path's release sequence runs (and logs)."""
        fake_guard.record = _make_record(
            state=STATE_KILLED,
            reason="killed:ladder",
            breach_at=time.time(),
            killed_at=time.time(),
            observed_peak_bytes=96 * 1024 * 1024,
        )
        await bash_module.bash.coroutine(command="echo hi", instance_id=INSTANCE)
        assert fake_guard.release_calls

    async def test_a_never_sampled_watch_is_not_released_by_the_lane(
        self, fake_guard, monkeypatch
    ):
        """The lane must not drop a watch whose membership is UNKNOWN."""
        fake_guard.record = _make_record(samples=0)
        await bash_module.bash.coroutine(command="echo hi", instance_id=INSTANCE)
        assert fake_guard.release_calls == []

    async def test_a_claimed_record_is_not_released_by_the_lane(
        self, fake_guard, monkeypatch
    ):
        """A CLAIMED record's release is deferred to the lane."""
        fake_guard.record = _make_record(samples=1, claim_token=5)
        await bash_module.bash.coroutine(command="echo hi", instance_id=INSTANCE)
        # The lane ASKS; the guard REFUSES (the claim defers the
        # release to the lane that owns the episode).
        assert fake_guard.release_calls
        assert fake_guard.release_refusals
        assert fake_guard.released == []

    async def test_a_live_watch_is_not_released_by_the_lane(
        self, fake_guard, monkeypatch
    ):
        """A live watch (a group member is still up) is NOT released."""
        fake_guard.record = _make_record(samples=1, group_member_count=2)
        await bash_module.bash.coroutine(command="echo hi", instance_id=INSTANCE)
        assert fake_guard.release_calls
        assert fake_guard.release_refusals
        assert fake_guard.released == []


# ── D5 — the release OUTCOME is logged (content-free) ─────────────────────


class TestReleaseLogging:
    async def test_the_ram_arm_release_outcome_is_logged_content_free(
        self, fake_guard, monkeypatch, caplog
    ):
        """D5 — the release's boolean is not discarded.

        The outcome rides a content-free log line: the tracking id and
        the reason CLASS only — never the argv, never an env value,
        never a secret.
        """
        import logging

        fake_guard.record = _make_record(
            state=STATE_KILLED,
            reason="killed:ladder",
            breach_at=time.time(),
            killed_at=time.time(),
            observed_peak_bytes=96 * 1024 * 1024,
        )
        with caplog.at_level(logging.INFO):
            out = await bash_module.bash.coroutine(
                command="echo SECRETARGVTOKEN", instance_id=INSTANCE
            )
        assert json.loads(out)["code"] == "ram_limit_exceeded"
        lines = [
            record.getMessage()
            for record in caplog.records
            if "ram-guard release" in record.getMessage()
        ]
        assert lines, "the release outcome was NOT logged"
        assert "class=" in lines[0]
        assert "tracking=" in lines[0]
        # Content-free: the argv (and anything shaped like a secret)
        # appears in NO log line.
        assert "SECRETARGVTOKEN" not in caplog.text

    async def test_the_release_class_is_content_free(self, fake_guard):
        """D5 — the class vocabulary is a fixed, content-free token set."""
        record = _make_record(samples=1)
        assert bash_module._ram_release_class(True, record) == "released"
        assert bash_module._ram_release_class(False, None) == "absent"
        record.claim_token = 3
        assert bash_module._ram_release_class(False, record) == "claimed"
        record.claim_token = None
        record.group_member_count = 2
        assert bash_module._ram_release_class(False, record) == "live-watch"
        record.group_member_count = 0
        record.state = STATE_KILLED
        assert bash_module._ram_release_class(False, record) == "already-terminal"


# ── T14 (a) — the REAL ballooning tree ─────────────────────────────────────


class TestRealBallooningTree:
    @pytest.mark.timeout(90)
    async def test_the_whole_tree_is_killed_and_the_envelope_is_returned(
        self, real_guard, tmp_path
    ):
        _require_headroom(PLANNED_BYTES * 4)
        script = _balloon_script(tmp_path)
        pidfile = tmp_path / "pids.txt"
        out = await bash_module.bash.coroutine(
            command=[
                "python3",
                str(script),
                str(CHILD_BYTES),
                str(CHILD_COUNT),
                str(pidfile),
            ],
            instance_id=INSTANCE,
        )
        payload = json.loads(out)
        assert payload["ok"] is False
        assert payload["code"] == "ram_limit_exceeded"
        assert payload["surface"] == "bash.exec"
        assert list(payload.keys()) == list(ENVELOPE_KEYS)
        assert payload["limit_bytes"] == CEILING_BYTES
        assert payload["reason"].startswith("killed:")
        # The DECIDING value clears the ceiling — ``confirmed_pss_bytes``
        # on the normal path, ``observed_peak_bytes`` on a fail-open.
        deciding = payload["confirmed_pss_bytes"]
        if payload["trigger_metric"] == "pss_confirm_unavailable" or deciding is None:
            deciding = payload["observed_peak_bytes"]
        assert deciding >= payload["limit_bytes"]
        # The RSS peak is reported separately, as a sampled lower bound.
        assert payload["peak_is_lower_bound"] is True
        # The under-report bound is the substrate's own MAX POSITIVE
        # sample-to-sample delta — 0 when the tree's footprint was
        # constant between the samples (the bash lane only publishes it).
        assert isinstance(payload["under_report_bound_bytes"], int)
        assert payload["under_report_bound_bytes"] >= 0
        # The envelope carries BOTH the configured ceiling and the
        # enforced trigger threshold.
        assert "limit_bytes" in payload
        assert "rearm_threshold_bytes" in payload

        # EVERY grandchild is dead.
        pids = _read_pids(pidfile)
        assert len(pids) == CHILD_COUNT
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if not any(_pid_alive(pid) for pid in pids):
                break
            await asyncio.sleep(0.05)
        for pid in pids:
            assert not _pid_alive(pid), f"grandchild {pid} survived"

        # The exit kind is the RAM one and the peak was converted once.
        history = get_pid_registry().drain_history()
        ram_rows = [row for row in history if row.exit_kind == EXIT_KIND_RAM_KILLED]
        assert len(ram_rows) == 1
        assert ram_rows[0].peak_rss_kb == round(payload["observed_peak_bytes"] / 1024)
        assert EXIT_KIND_TIMEOUT not in {row.exit_kind for row in history}

    @pytest.mark.timeout(90)
    async def test_the_leaderless_tree_is_still_watched_and_killed(
        self, real_guard, tmp_path
    ):
        """A shell that backgrounds a ballooning grandchild and exits."""
        # The grandchild alone must clear the ceiling (it is the only
        # member left after the shell exits).
        child_bytes = 96 * 1024 * 1024
        _require_headroom(child_bytes * 4)
        script = _balloon_script(tmp_path)
        pidfile = tmp_path / "pids.txt"
        out = await bash_module.bash.coroutine(
            command=f"python3 {script} {child_bytes} 1 {pidfile} &",
            instance_id=INSTANCE,
        )
        # The shell exited successfully — the ordinary return.
        assert "EXIT CODE: 0" in out
        assert "ram_limit_exceeded" not in out

        # The guard entry SURVIVES the success path while a group member
        # is live (the watch is not ended by the lane's exit).
        assert real_guard.health()["registry_depth"] >= 1

        # The grandchild is still watched — and it is killed.
        deadline = time.monotonic() + 20.0
        pids: list[int] = []
        while time.monotonic() < deadline:
            pids = _read_pids(pidfile)
            if pids and not any(_pid_alive(pid) for pid in pids):
                break
            await asyncio.sleep(0.05)
        assert pids, "the grandchild never reported its pid"
        for pid in pids:
            assert not _pid_alive(pid), f"grandchild {pid} survived"
