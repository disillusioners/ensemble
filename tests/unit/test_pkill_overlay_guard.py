"""Unit tests for the rogue-containment pkill overlay guard (pure function)."""

import os

from daemon.tools.bash import _pkill_overlay_violation


def test_non_kill_command_passes():
    assert _pkill_overlay_violation("echo hello world") is None
    assert _pkill_overlay_violation("ls -la /tmp") is None


def test_kill_without_live_pattern_passes():
    assert _pkill_overlay_violation("pkill -f my-scratch-daemon") is None
    assert _pkill_overlay_violation("pkill -f ens-e2e-scrub-probe") is None


def test_kill_live_pattern_refused_without_fence():
    assert _pkill_overlay_violation("pkill -f ensemble-prod") == "ensemble-prod"
    assert _pkill_overlay_violation("systemctl stop ensemble-daemon") == "ensemble-daemon"
    assert _pkill_overlay_violation("fuser -k 9797/tcp") == "9797"
    assert _pkill_overlay_violation("pkill -f /home/nea/agents-ensemble") == "agents-ensemble"


def test_kill_live_pattern_allowed_with_env_overlay_fence():
    env = {"ENSEMBLE_FENCED_TEST_CONTEXT": "1"}
    assert _pkill_overlay_violation("pkill -f ensemble-prod", env=env) is None


def test_kill_live_pattern_allowed_with_ambient_fence(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_FENCED_TEST_CONTEXT", "1")
    assert _pkill_overlay_violation("pkill -f ensemble-prod") is None


def test_list_command_normalized():
    assert _pkill_overlay_violation(["pkill", "-f", "ensemble-prod"]) == "ensemble-prod"
