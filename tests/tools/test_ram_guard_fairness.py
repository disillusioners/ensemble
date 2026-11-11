"""The FORK-TREE FAIRNESS pack (phase6 T16 / test-plan U4).

**The single most important fairness proof in the suite.**  A real
fork-WITHOUT-exec tree (a parent allocates ONE buffer and forks 9 children
that only sleep) has a summed RSS that OVER-counts the shared pages once
per process — but its summed PSS counts them ~once.  The ruled hybrid
must therefore take the FAIR path: the tree is **NOT killed** (the PSS
confirm clears the ceiling), even though the RSS trigger fired.

**Counter-mutation (mandatory):** break the confirm (always fail-open) ⇒
the SAME tree IS killed ⇒ this test's "not killed" assertion REDDENS.  The
counter-mutation is exercised in-process by monkeypatching the guard's
``_run_confirm`` to force the fail-open branch — if the fairness gate were
bypassed, the tree would die and the assertion would fire.

The pack runs under the T2 fence helper (:mod:`tests.helpers.ram_guard_fence`):
``ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`` pinned, ``POSTGRES_*`` scrubbed
per-variable, the ``ENSEMBLE_AUTH_*`` family scrubbed all-or-none, and a
``mktemp`` FAKE_HOME — each ASSERTED to have applied (not merely invoked).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from daemon.services.ram_guard import set_ram_guard
from daemon.services.ram_guard.guard import RamGuard
from daemon.services.resource_monitor.pid_registry import reset_pid_registry_for_tests
from tests.helpers.ram_guard_fence import (
    apply_fence,
    assert_fence_applied,
)
from tests.helpers.ram_guard_harness import (
    BalloonSpec,
    HostMemoryShortage,
    assert_stat_line_shape,
    pid_alive,
    prepare_balloon,
    require_allocation_fits,
    wait_dead,
    wait_for_pids,
)

MIB = 1024 * 1024
INSTANCE = "inst-ram-guard-fairness"

#: The parent's ONE buffer — the shared-page source.
PARENT_BYTES = 16 * MIB
#: The fork count (the E6 9-member shape + the parent).
FORK_COUNT = 9
#: The injected ceiling: ABOVE the summed PSS (~16 MiB), BELOW the summed
#: RSS (~160 MiB).  This is the fairness discriminator.
CEILING_BYTES = 48 * MIB

#: How long the fair tree HOLDS after forking.  Kept SHORT so the leg is
#: genuinely fast: the tree lives long enough for the guard to fire the RSS
#: trigger and run the PSS confirm (several 100 ms polls), then exits
#: NORMALLY.  The bash-lane timeout (:data:`BASH_TIMEOUT_S`) is a safety net
#: far above this — the command completes by itself, making the
#: "not killed" assertion stronger (a real success return, not a timeout).
FORK_HOLD_S = 6
#: The bash-lane timeout — a bounded safety net above :data:`FORK_HOLD_S`.
BASH_TIMEOUT_S = 20


@pytest.fixture(autouse=True)
def _fence(monkeypatch, tmp_path):
    """T2 — apply the fence around every spawning test; assert it held."""
    fake_home, auth_names = apply_fence(monkeypatch, tmp_path)
    yield
    assert_fence_applied(fake_home, auth_names)


@pytest.fixture(autouse=True)
def _clean_pid_registry():
    reset_pid_registry_for_tests()
    yield
    reset_pid_registry_for_tests()


@pytest.fixture
def real_guard():
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


def _spec() -> BalloonSpec:
    return BalloonSpec(
        ceiling_bytes=CEILING_BYTES,
        child_bytes=PARENT_BYTES,
        child_count=FORK_COUNT,
    )


def _spawn_fair_tree(tmp_path: Path) -> tuple[list[int], object, str]:
    spec = _spec()
    try:
        require_allocation_fits(spec)
    except HostMemoryShortage as exc:
        pytest.skip(str(exc))
    tree = prepare_balloon(tmp_path, fair=True)
    # The command: the fair script allocates PARENT_BYTES, forks, then HOLDS
    # for FORK_HOLD_S before exiting normally.
    cmd = (
        f"{sys.executable} -u {tree.script} {PARENT_BYTES} "
        f"{FORK_COUNT} {tree.pidfile} {FORK_HOLD_S}"
    )
    return ([], tree, cmd)


def _teardown_fair_tree(tree) -> None:
    """D-4 — register every recorded pid, then ALWAYS SIGKILL the tree.

    Mirrors the mutation leg's correct shape: without this, an assertion
    failure relies on the bash lane's timeout group-kill and the harness
    backstop never runs (T1: teardown ALWAYS runs).
    """
    for pid in tree.read_pids():
        tree.register(pid)
    tree.kill_all()


class TestForkTreeFairness:
    @pytest.mark.asyncio
    @pytest.mark.timeout(90)
    async def test_a_fork_tree_over_counted_by_rss_is_not_killed(
        self, real_guard, tmp_path
    ):
        """U4 — summed RSS crosses the ceiling, summed PSS does not ⇒ the
        tree is NOT killed (the FAIR path is taken, not the RSS shortcut)."""
        import asyncio
        import importlib

        bash_module = importlib.import_module("daemon.tools.bash")
        assert_stat_line_shape()

        _pids, tree, cmd = _spawn_fair_tree(tmp_path)
        try:
            out = await bash_module.bash.coroutine(
                command=cmd, instance_id=INSTANCE, timeout=BASH_TIMEOUT_S
            )
        finally:
            _teardown_fair_tree(tree)
        # The command ran to completion (the tree was never killed).
        assert "ram_limit_exceeded" not in out, out
        # The breach WAS detected (the RSS trigger fired) — the fairness
        # path is what saved the tree.
        counters = real_guard.counters_snapshot()
        assert counters["ram_breaches_detected"] >= 1
        assert counters["ram_kills"] == 0
        # At least one OVERCOUNTED re-arm proves the PSS confirm ran and
        # found the tree fair.
        assert counters["ram_overcounted"] >= 1

    @pytest.mark.asyncio
    @pytest.mark.timeout(90)
    async def test_the_broken_confirm_counter_mutation_kills_the_fair_tree(
        self, real_guard, tmp_path, monkeypatch
    ):
        """COUNTER-MUTATION — break the confirm (always fail-open) ⇒ the
        SAME fair tree IS killed ⇒ the fairness test's "not killed"
        assertion would redden.

        This proves the fairness test is NOT vacuous: the only thing
        standing between the tree and death is the PSS confirm.
        """
        import importlib

        bash_module = importlib.import_module("daemon.tools.bash")
        # MUTATION: force the confirm to fail-open on EVERY call.
        monkeypatch.setattr(
            real_guard,
            "_run_confirm",
            lambda record: (None, False, 0.0),
        )
        _pids, tree, cmd = _spawn_fair_tree(tmp_path)
        try:
            out = await bash_module.bash.coroutine(
                command=cmd, instance_id=INSTANCE, timeout=BASH_TIMEOUT_S
            )
        finally:
            _teardown_fair_tree(tree)
        # The broken confirm kills the fair tree — the RSS shortcut.
        assert "ram_limit_exceeded" in out, out
        assert real_guard.counters_snapshot()["ram_kills"] >= 1


class TestHarnessBounds:
    def test_the_harness_refuses_an_allocation_over_the_per_tree_cap(self):
        """T1 — the harness refuses an allocation above the 256 MiB cap."""
        spec = BalloonSpec(
            ceiling_bytes=64 * MIB,
            child_bytes=64 * MIB,
            child_count=5,  # 320 MiB > the 256 MiB cap
        )
        with pytest.raises(HostMemoryShortage):
            require_allocation_fits(spec)

    def test_the_stat_line_shape_is_pinned_against_the_reader(self):
        """T1 / test-plan §1.1 — the documented stat-line shape agrees with
        the reader's own private offsets."""
        assert_stat_line_shape()

    def test_an_unreadable_meminfo_refuses_the_spawn_fail_closed(self, monkeypatch):
        """D-6 — an UNREADABLE ``/proc/meminfo`` REFUSES the spawn.

        A silent pass on an unreadable meminfo would let a balloon run
        blind — the exact OOM hazard the harness exists to prevent.
        """
        import tests.helpers.ram_guard_harness as harness

        def _boom(*_a, **_k):
            raise OSError("meminfo unreadable")

        monkeypatch.setattr(harness.Path, "read_text", _boom)
        assert harness.mem_available_bytes() == -1
        with pytest.raises(HostMemoryShortage):
            require_allocation_fits(_spec())

    def test_a_zero_meminfo_refuses_the_spawn_fail_closed(self, monkeypatch):
        """D-6 — a genuine ZERO MemAvailable also refuses (never a silent pass)."""
        import tests.helpers.ram_guard_harness as harness

        monkeypatch.setattr(harness, "mem_available_bytes", lambda: 0)
        with pytest.raises(HostMemoryShortage):
            require_allocation_fits(_spec())
