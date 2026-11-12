"""Mutation-proven overlay pins (component A/B census): every pattern family."""

from daemon.tools.bash import _pkill_overlay_violation


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


def test_scratch_sentinel_allowed_unfenced():
    assert _pkill_overlay_violation("pkill -f ens-e2e-scrub-probe") is None
    assert _pkill_overlay_violation("pkill -f my-scratch-daemon") is None


def test_overlay_fence_does_not_allow_live_pattern_B6():
    """B6: the overlay is not a fence channel (ambient-only)."""
    env = {"ENSEMBLE_FENCED_TEST_CONTEXT": "1"}
    for pat in ("ensemble-prod", "ensemble-daemon", "9797", "9798", "agents-ensemble"):
        assert _pkill_overlay_violation(f"pkill -f {pat}", env=env) == pat


def test_ambient_fence_allows(monkeypatch):
    """The daemon-set ambient env IS the fence channel."""
    monkeypatch.setenv("ENSEMBLE_FENCED_TEST_CONTEXT", "1")
    assert _pkill_overlay_violation("pkill -f ensemble-prod") is None
    assert _pkill_overlay_violation("fuser -k 9797/tcp") is None


def test_no_ambient_fence_refuses(monkeypatch):
    monkeypatch.delenv("ENSEMBLE_FENCED_TEST_CONTEXT", raising=False)
    assert _pkill_overlay_violation("pkill -f ensemble-prod") == "ensemble-prod"


def test_non_kill_command_with_live_pattern_passes():
    # No kill-like token => passes even unfenced.
    assert _pkill_overlay_violation("echo ensemble-prod 9797") is None


def test_list_form():
    assert _pkill_overlay_violation(["fuser", "-k", "9797/tcp"]) == "9797"
