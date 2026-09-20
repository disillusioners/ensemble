"""Unit tests for the phase-3 tmp-image cleanup config knobs.

clipboard-image-chat / Tasks 2 + 6 acceptance. Pins:

* defaults: interval 3600 (pinned hourly per architect §7) and
  retention 30 days;
* env overrides (O1 long-form, ``SERVICES_TMP_IMAGE_CLEANUP_*``)
  honored for BOTH knobs;
* out-of-range values (interval=0, retention=0) FAIL FAST at boot
  via pydantic ValidationError (``Field(ge=1)``; 0 retention would
  delete everything on the first tick);
* the kill-switch is DELETED (architect amendment #15 — owner HARD
  POLICY): no ``tmp_image_cleanup_enabled`` field in the schema and
  ``SERVICES_TMP_IMAGE_CLEANUP_ENABLED`` is NOT honored.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from daemon.config import ServicesConfig

_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "daemon"
    / "config.py"
)


class TestCleanupDefaults:
    def test_default_interval_is_3600_pinned_hourly(self):
        cfg = ServicesConfig()
        assert cfg.tmp_image_cleanup_interval_seconds == 3600, (
            "interval default must be 3600s — PINNED hourly per "
            "architect §7 (the plan draft's 86400 was overruled)"
        )

    def test_default_retention_is_30_days(self):
        cfg = ServicesConfig()
        assert cfg.tmp_image_cleanup_retention_days == 30, (
            "retention default must be 30 days (the plan's objective "
            "value; R10 activation checklist keys off it)"
        )


class TestCleanupEnvOverrides:
    def test_interval_env_override(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(
            "SERVICES_TMP_IMAGE_CLEANUP_INTERVAL_SECONDS", "60"
        )
        cfg = ServicesConfig()
        assert cfg.tmp_image_cleanup_interval_seconds == 60

    def test_retention_env_override(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(
            "SERVICES_TMP_IMAGE_CLEANUP_RETENTION_DAYS", "7"
        )
        cfg = ServicesConfig()
        assert cfg.tmp_image_cleanup_retention_days == 7


class TestCleanupFailFastBounds:
    @pytest.mark.parametrize(
        "field_name",
        [
            "tmp_image_cleanup_interval_seconds",
            "tmp_image_cleanup_retention_days",
        ],
    )
    @pytest.mark.parametrize("bad_value", [0, -5])
    def test_zero_and_negative_fail_fast(
        self, field_name: str, bad_value: int
    ):
        # ge=1 on BOTH fields: interval 0 = spin; retention 0 would
        # delete EVERYTHING on the first tick.
        with pytest.raises(ValidationError) as exc_info:
            ServicesConfig(**{field_name: bad_value})
        assert field_name in str(exc_info.value), (
            "ValidationError must name the field so operators can "
            "find it in the boot log"
        )


class TestKillSwitchDeleted:
    """Architect amendment #15 — the kill-switch MUST NOT exist.

    Owner HARD POLICY (codified in ``job_lock_sweep.py``): a
    cleanup toggle's unique failure mode is silent permanent storage
    growth when flipped by accident. The retention-days knob IS the
    operator lever. Asserting absence prevents accidental
    re-addition.
    """

    def test_no_enabled_field_in_schema(self):
        assert "tmp_image_cleanup_enabled" not in ServicesConfig.model_fields

    def test_enabled_env_var_not_honored(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # Setting the deleted env var must NOT conjure a field
        # (pydantic-settings ignores non-schema prefixed vars).
        monkeypatch.setenv("SERVICES_TMP_IMAGE_CLEANUP_ENABLED", "0")
        cfg = ServicesConfig()
        assert not hasattr(cfg, "tmp_image_cleanup_enabled")

    def test_config_source_has_no_enabled_field(self):
        # Doc-truth pin — daemon/config.py must not carry a FIELD
        # declaration for the kill-switch. (The explanatory comment
        # documenting the deletion MAY name it; the declaration may
        # not.)
        src = _CONFIG_PATH.read_text(encoding="utf-8")
        assert "tmp_image_cleanup_enabled: int" not in src
        assert "tmp_image_cleanup_enabled: bool" not in src
        # The env var must never be an OVERRIDE path in a description
        # (the real knobs carry "Override via SERVICES_TMP_IMAGE_CLEANUP_*").
        assert "Override via SERVICES_TMP_IMAGE_CLEANUP_ENABLED" not in src
