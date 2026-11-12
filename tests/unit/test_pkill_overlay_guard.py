"""Unit tests for the rogue-containment pkill overlay guard (pure function)."""

import os

from daemon.tools.bash import _pkill_overlay_violation


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
