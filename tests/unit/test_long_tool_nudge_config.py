"""T10 — LongToolCallNudgeConfig tests (config + env + fail-fast).

Relocated from phase 2 U14-U16 per the B1 re-slice (AM-8).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from daemon.config import LongToolCallNudgeConfig

_ENV_KEYS = (
    "LONG_TOOL_NUDGE_ENABLED",
    "LONG_TOOL_NUDGE_INTERVAL_SECONDS",
    "LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


class TestLongToolNudgeConfigDefaults:
    def test_defaults(self):
        cfg = LongToolCallNudgeConfig()
        assert cfg.enabled is True
        assert cfg.interval_seconds == 60
        assert cfg.default_threshold_seconds == 900


class TestLongToolNudgeConfigEnvOverrides:
    def test_env_overrides_all_three_fields(self, monkeypatch):
        monkeypatch.setenv("LONG_TOOL_NUDGE_ENABLED", "false")
        monkeypatch.setenv("LONG_TOOL_NUDGE_INTERVAL_SECONDS", "30")
        monkeypatch.setenv("LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS", "1200")
        cfg = LongToolCallNudgeConfig()
        assert cfg.enabled is False
        assert cfg.interval_seconds == 30
        assert cfg.default_threshold_seconds == 1200


class TestLongToolNudgeConfigFailFastAtBoot:
    def test_interval_zero_rejected(self, monkeypatch):
        monkeypatch.setenv("LONG_TOOL_NUDGE_INTERVAL_SECONDS", "0")
        with pytest.raises(ValidationError):
            LongToolCallNudgeConfig()

    def test_threshold_above_hard_max_rejected(self, monkeypatch):
        monkeypatch.setenv("LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS", "2000")
        with pytest.raises(ValidationError):
            LongToolCallNudgeConfig()

    def test_threshold_at_hard_max_accepted(self, monkeypatch):
        monkeypatch.setenv("LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS", "1800")
        cfg = LongToolCallNudgeConfig()
        assert cfg.default_threshold_seconds == 1800


class TestFloorAndCeilingConstants:
    """AD-30: the floor/ceiling constants have ONE canonical home."""

    def test_identity_import_from_canonical_home(self):
        from daemon.services import long_tool_nudge as canonical

        assert canonical.HARD_MAX_THRESHOLD_SECONDS == 1800
        assert canonical.MIN_THRESHOLD_SECONDS == 60
        assert canonical.STALE_STAMP_TTL_SECONDS == 7200

    def test_top_level_config_wiring_present(self):
        # The plan's working name "EnsembleConfig" is ``Config`` in
        # this tree — the wiring field must sit on the real class.
        from daemon.config import Config

        assert "long_tool_nudge" in Config.model_fields
