"""Mutation-proven overlay pins (component A/B census): every pattern family."""

import importlib
import inspect

from daemon.tools.bash import _pkill_overlay_violation
from daemon.tools.proc_tools import create_proc_tools


def test_name_family_refused_unfenced():
    for pat in ("ensemble-prod", "ensemble-daemon", "agents-ensemble"):
        assert _pkill_overlay_violation(f"pkill -f {pat}") == pat


def test_unit_ref_family_refused_unfenced():
    assert _pkill_overlay_violation("systemctl stop ensemble-daemon") == "ensemble-daemon"


def test_install_root_path_family_refused_unfenced():
    assert _pkill_overlay_violation("pkill -f /home/nea/agents-ensemble") == "agents-ensemble"


def test_port_family_refused_unfenced():
    assert _pkill_overlay_violation("fuser -k 9797/tcp") == "9797"
    assert _pkill_overlay_violation("fuser -k 9798/tcp") == "9798"
    # A1 hygiene: the demo port joined the family (§5.9a). Counter-mutant
    # HY-M1 — removing the ("7979", r"\b7979\b") tuple entry reddens this.
    assert _pkill_overlay_violation("fuser -k 7979/tcp") == "7979"


def test_scratch_sentinel_allowed_unfenced():
    assert _pkill_overlay_violation("pkill -f ens-e2e-scrub-probe") is None
    assert _pkill_overlay_violation("pkill -f my-scratch-daemon") is None


def test_overlay_fence_does_not_allow_live_pattern_B6():
    """B6: the overlay is not a fence channel (ambient-only)."""
    env = {"ENSEMBLE_FENCED_TEST_CONTEXT": "1"}
    for pat in ("ensemble-prod", "ensemble-daemon", "9797", "9798", "7979", "agents-ensemble"):
        assert _pkill_overlay_violation(f"pkill -f {pat}", env=env) == pat


def test_ambient_fence_allows(monkeypatch):
    """The daemon-set ambient env IS the fence channel."""
    monkeypatch.setenv("ENSEMBLE_FENCED_TEST_CONTEXT", "1")
    assert _pkill_overlay_violation("pkill -f ensemble-prod") is None
    assert _pkill_overlay_violation("fuser -k 9797/tcp") is None
    # A1 hygiene: the ambient fence exempts the new token too (§5.9a).
    assert _pkill_overlay_violation("fuser -k 7979/tcp") is None


def test_no_ambient_fence_refuses(monkeypatch):
    monkeypatch.delenv("ENSEMBLE_FENCED_TEST_CONTEXT", raising=False)
    assert _pkill_overlay_violation("pkill -f ensemble-prod") == "ensemble-prod"


def test_non_kill_command_with_live_pattern_passes():
    # No kill-like token => passes even unfenced.
    assert _pkill_overlay_violation("echo ensemble-prod 9797") is None


def test_list_form():
    assert _pkill_overlay_violation(["fuser", "-k", "9797/tcp"]) == "9797"
    # A1 hygiene: the list-form normalization carries the new token (§5.9a).
    assert _pkill_overlay_violation(["fuser", "-k", "7979/tcp"]) == "7979"


# ---------------------------------------------------------------------------
# HY-2 (A1 hygiene): the proc lane refuses THROUGH THE DELEGATION
# ---------------------------------------------------------------------------


async def test_proc_lane_delegates_to_bash_predicate_HY2(monkeypatch):
    r"""HY-2: proc_tools has NO duplicated predicate — proc_run imports
    bash's ``_pkill_overlay_violation`` at call time (function-local
    ``from daemon.tools.bash import ...`` inside ``create_proc_tools``).
    Counter-mutant HY-M2 (break the delegation: the proc predicate stops
    seeing bash tokens — local duplicate or dropped import) reddens this:
    (a) structurally, the bash import disappears from the factory source;
    (b) behaviorally, proc_run's refusal must follow a STUBBED shared
    function (proving the import is the live data path) and scratch
    commands must pass through the REAL shared predicate (spy-wrapped)
    to the timeout validation — never spawning."""
    bash_mod = importlib.import_module("daemon.tools.bash")
    proc_mod = importlib.import_module("daemon.tools.proc_tools")

    # (a) structural: the delegation import is present in the factory.
    src = inspect.getsource(proc_mod.create_proc_tools)
    assert "from daemon.tools.bash import _pkill_overlay_violation" in src

    pr = next(t for t in create_proc_tools("hy2-pin-instance") if t.name == "proc_run")

    # capture the REAL shared predicate BEFORE any monkeypatching — the
    # pass leg must wrap the genuine function, not a prior stub.
    real = bash_mod._pkill_overlay_violation

    # (b) behavioral, refusal leg: stub the SHARED function; proc_run's
    # verdict must carry the stub's return value.
    stub_calls: list = []

    def _sentinel(command, env=None):
        stub_calls.append(command)
        return "delegated-sentinel"

    monkeypatch.setattr(bash_mod, "_pkill_overlay_violation", _sentinel)
    refused = await pr.coroutine(command="pkill -f ens-e2e-scrub-probe")
    assert "delegated-sentinel" in refused
    assert stub_calls == ["pkill -f ens-e2e-scrub-probe"]

    # (b) behavioral, pass leg: spy wrapping the REAL predicate; a
    # scratch-sentinel command passes the guard and falls through to the
    # timeout validation (timeout=-1 errors BEFORE any spawn).
    spy_calls: list = []

    def _spy(command, env=None):
        spy_calls.append(command)
        return real(command, env)

    monkeypatch.setattr(bash_mod, "_pkill_overlay_violation", _spy)
    result = await pr.coroutine(command="pkill -f ens-e2e-scrub-probe", timeout=-1)
    assert "timeout must be >= 0" in result
    assert "refused by the rogue-containment pkill overlay guard" not in result
    assert len(spy_calls) == 1


# ---------------------------------------------------------------------------
# HY-3 (A1 hygiene): bash refusal text is ambient-only (drift fix)
# ---------------------------------------------------------------------------


async def test_bash_refusal_text_names_ambient_channel_only_HY3():
    r"""HY-3: the bash guard's refusal text must NOT advertise the dead
    agent-settable env-overlay remedy (B6 killed that channel — the fence
    is ambient-only) and must name the DAEMON-AMBIENT channel instead,
    matching the proc_tools exemplar shape. Counter-mutant HY-M3 (restore
    the dead ``env={"ENSEMBLE_FENCED_TEST_CONTEXT": "1"}`` advertisement
    in the refusal string) reddens the forbidden-phrase asserts; dropping
    the ambient remedy reddens the positive assert. The refusal returns
    BEFORE any command execution, so invoking the tool here is inert."""
    bash_mod = importlib.import_module("daemon.tools.bash")
    text = await bash_mod.bash.coroutine(command="pkill -f ensemble-prod")
    assert text.startswith(
        "ERROR: refused by the rogue-containment pkill overlay guard"
    )
    # forbidden: the dead tool-overlay advertisement
    assert 'env={"ENSEMBLE_FENCED_TEST_CONTEXT": "1"}' not in text
    assert "in the tool env" not in text
    # required: the daemon-ambient channel remedy
    assert "ENSEMBLE_FENCED_TEST_CONTEXT=1 in the daemon environment" in text


# ---------------------------------------------------------------------------
# HY-4 (A1 hygiene): proc refusal text stays ambient-only
# ---------------------------------------------------------------------------


async def test_proc_refusal_text_stays_ambient_only_HY4():
    r"""HY-4: the proc lane's refusal text already names the AMBIENT
    channel only (the exemplar bash.py was rewritten to match) — pinned
    to prevent drift toward the dead overlay advertisement. Counter-mutant
    HY-M4 (add an overlay / ``env={...}`` remedy to the proc refusal
    text) reddens the forbidden-phrase asserts. The refusal returns
    BEFORE any spawn, so invoking proc_run here is inert."""
    pr = next(t for t in create_proc_tools("hy4-pin-instance") if t.name == "proc_run")
    text = await pr.coroutine(command="pkill -f ensemble-prod")
    assert text.startswith(
        "Error: refused by the rogue-containment pkill overlay guard"
    )
    # forbidden: the dead tool-overlay advertisement
    assert 'env={"ENSEMBLE_FENCED_TEST_CONTEXT": "1"}' not in text
    assert "in the tool env" not in text
    # required: the daemon-ambient channel remedy
    assert "ENSEMBLE_FENCED_TEST_CONTEXT=1 in the daemon environment" in text
