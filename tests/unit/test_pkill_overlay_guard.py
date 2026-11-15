"""Unit tests for the rogue-containment pkill overlay guard (pure function)."""

import asyncio
import os

import pytest

from daemon.tools.bash import _pkill_overlay_violation


# ---------------------------------------------------------------------------
# STRUCTURAL spawn-seam neuter — record-instead-of-spawn (HY-M2 incident fix)
# ---------------------------------------------------------------------------
# This file pins the guard's PURE predicate (no tool coroutine is invoked
# here today — every pin calls ``_pkill_overlay_violation`` directly, which
# cannot spawn). The autouse recorder below is defense-in-depth for the
# FAMILY: if a future pin in THIS file ever invokes the real bash/proc tool
# coroutine with a command, its spawn seam is already neutered — under any
# mutant, including refusal-gate-bypassing ones. Mirrors the fixture in
# test_pkill_overlay_pins.py (module-local copies; tests/conftest.py is
# deliberately NOT touched). NOTHING is ever executed.


class _RecordedSpawnStub:
    """Inert stand-in for ``asyncio.subprocess.Process`` — never a process."""

    def __init__(self, seq: int) -> None:
        self.pid = 2_000_000 + seq
        self.returncode = 0
        self.stdout = None
        self.stderr = None

    async def wait(self) -> int:
        return self.returncode


@pytest.fixture(autouse=True)
def _spawn_seam_recorder(monkeypatch):
    """Record-instead-of-spawn tripwire for every pin in this file."""
    spawns: list = []
    seq = 0

    async def _record_exec(*args, **kwargs):
        nonlocal seq
        seq += 1
        spawns.append({"argv": list(args), "cwd": kwargs.get("cwd")})
        return _RecordedSpawnStub(seq)

    async def _record_shell(command, *args, **kwargs):
        nonlocal seq
        seq += 1
        spawns.append({"argv": [command], "cwd": kwargs.get("cwd")})
        return _RecordedSpawnStub(seq)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _record_exec, raising=True)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _record_shell, raising=True)
    yield
    assert not spawns, (
        "STRUCTURAL SPAWN GUARD tripped (test_pkill_overlay_guard): the recorder "
        f"captured {len(spawns)} spawn attempt(s): {spawns!r}. NOTHING was "
        "executed — the recorder returned an inert stub — so this run is "
        "harmless; but a spawn from this pure-predicate file means a pin "
        "started invoking a real tool coroutine and its guard is bypassed."
    )


def test_non_kill_command_passes():
    assert _pkill_overlay_violation("echo hello world") is None
    assert _pkill_overlay_violation("ls -la /tmp") is None
    assert _pkill_overlay_violation("grep 9797 /tmp/foo") is None


def test_skill_false_positive_passes():
    assert _pkill_overlay_violation("echo skill management") is None


def test_port_false_positive_passes():
    assert _pkill_overlay_violation("kill -0 19797") is None


def test_kill_without_live_pattern_passes():
    assert _pkill_overlay_violation("pkill -f my-scratch-daemon") is None
    assert _pkill_overlay_violation("pkill -f ens-e2e-scrub-probe") is None


def test_kill_live_pattern_refused_without_fence():
    assert _pkill_overlay_violation("pkill -f ensemble-prod") == "ensemble-prod"
    assert _pkill_overlay_violation("pkill -f ensemble-daemon") == "ensemble-daemon"
    assert _pkill_overlay_violation("pkill -f ensemble-main") == "ensemble-main"
    assert _pkill_overlay_violation("pkill -f ensemble-demo") == "ensemble-demo"
    assert _pkill_overlay_violation("fuser -k 9797/tcp") == "9797"
    assert _pkill_overlay_violation("pkill -f /home/nea/agents-ensemble") == "agents-ensemble"


def test_systemctl_mutating_refused_readonly_allowed():
    assert _pkill_overlay_violation("systemctl --user stop ensemble-main.service") == "ensemble-main"
    assert _pkill_overlay_violation("systemctl --user restart ensemble-demo.service") == "ensemble-demo"
    assert _pkill_overlay_violation("systemctl --user status ensemble-main.service") is None
    assert _pkill_overlay_violation("systemctl --user is-active ensemble-demo.service") is None
    assert _pkill_overlay_violation("systemctl --user show ensemble-main.service") is None


def test_kill_substitution_refused():
    assert _pkill_overlay_violation("kill -9 $(pgrep -f ensemble-prod)") == "ensemble-prod"


def test_xargs_kill_with_live_token_refused():
    assert _pkill_overlay_violation("echo ensemble-prod | xargs kill") is not None


def test_env_overlay_fence_is_IGNORED_B6():
    """fix-round 2 / B6: the agent-settable tool-call env overlay is NOT a
    fence channel — otherwise the guard is pattern-only, not containment."""
    env = {"ENSEMBLE_FENCED_TEST_CONTEXT": "1"}
    assert _pkill_overlay_violation("pkill -f ensemble-prod", env=env) == "ensemble-prod"
    assert (
        _pkill_overlay_violation("systemctl --user stop ensemble-main.service", env=env)
        == "ensemble-main"
    )


def test_env_overlay_fence_without_ambient_still_refused(monkeypatch):
    monkeypatch.delenv("ENSEMBLE_FENCED_TEST_CONTEXT", raising=False)
    assert _pkill_overlay_violation("pkill -f 9797", env={"ENSEMBLE_FENCED_TEST_CONTEXT": "1"}) == "9797"


def test_kill_live_pattern_allowed_with_ambient_fence(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_FENCED_TEST_CONTEXT", "1")
    assert _pkill_overlay_violation("pkill -f ensemble-prod") is None


def test_list_command_normalized():
    assert _pkill_overlay_violation(["pkill", "-f", "ensemble-prod"]) == "ensemble-prod"


def test_input_channel_refused():
    # The bash tool applies the same predicate to `input=`; verify the
    # pure function refuses a kill+live-token stdin payload.
    assert _pkill_overlay_violation("pkill -f ensemble-prod") == "ensemble-prod"


# ---------------------------------------------------------------------------
# HY-1 (A1 hygiene): demo port 7979 joined the live port tokens
# ---------------------------------------------------------------------------


def test_7979_token_refused_unfenced_HY1():
    r"""HY-1: the 7979 token is a live pattern. Counter-mutant HY-M1 —
    removing the ("7979", r"\b7979\b") tuple entry reddens this (the
    predicate returns None where "7979" is required)."""
    assert _pkill_overlay_violation("fuser -k 7979/tcp") == "7979"
    assert _pkill_overlay_violation("pkill -f 7979") == "7979"


def test_7979_whole_word_negative_passes_HY1():
    r"""HY-1: port tokens match as WHOLE WORDS only. The matrix negative
    (19799) passes trivially; 17979 CONTAINS "7979" mid-word, so this pin
    reddens under the counter-mutation that strips the \b boundaries
    (naive substring match) — removal of the tuple (HY-M1) is separately
    reddened by test_7979_token_refused_unfenced_HY1."""
    assert _pkill_overlay_violation("kill -0 19799") is None
    assert _pkill_overlay_violation("kill -0 17979") is None


def test_7979_allowed_with_ambient_fence_HY1(monkeypatch):
    """HY-1: the daemon-set ambient fence still exempts a 7979 kill, and
    the agent-settable overlay does NOT (B6 semantics carry to the new
    token). Counter-mutant HY-M1 (tuple removal) reddens the paired
    refusal leg; an overlay-as-fence regression reddens the first assert
    here (asserted with the ambient var absent so ambient cannot win)."""
    monkeypatch.delenv("ENSEMBLE_FENCED_TEST_CONTEXT", raising=False)
    env = {"ENSEMBLE_FENCED_TEST_CONTEXT": "1"}
    assert _pkill_overlay_violation("fuser -k 7979/tcp", env=env) == "7979"
    monkeypatch.setenv("ENSEMBLE_FENCED_TEST_CONTEXT", "1")
    assert _pkill_overlay_violation("fuser -k 7979/tcp") is None
