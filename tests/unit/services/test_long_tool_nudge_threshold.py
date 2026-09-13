"""T2 + T11 — LongToolNudgeScanner threshold resolution tests.

Pins the canonical threshold precedence chain rungs 2-4 (AD-41):
per-child metadata key with the read-side floor (AD-38), env default
fallback, and the ``min(·, hard_max)`` clamp. Also pins the scanner
ctor validation (watchdog-style fail-fast).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from daemon.services.long_tool_nudge import (
    HARD_MAX_THRESHOLD_SECONDS,
    MIN_THRESHOLD_SECONDS,
    LongToolNudgeScanner,
    _LONG_TOOL_REGISTRY,
)


def _scanner(metadata_value=None, **overrides) -> LongToolNudgeScanner:
    repo = MagicMock()
    repo.get_metadata_value = MagicMock(return_value=metadata_value)
    kwargs = dict(
        instance_repository=repo,
        manager=None,
        registry=_LONG_TOOL_REGISTRY,
        interval_seconds=60,
        default_threshold_seconds=900,
    )
    kwargs.update(overrides)
    return LongToolNudgeScanner(**kwargs)


class TestThresholdResolution:
    def test_metadata_value_used_when_in_range(self):
        scanner = _scanner(metadata_value=1200)
        assert scanner._resolve_threshold("child-1") == 1200

    def test_metadata_value_above_hard_max_capped(self):
        scanner = _scanner(metadata_value=3600)
        assert scanner._resolve_threshold("child-1") == HARD_MAX_THRESHOLD_SECONDS

    def test_metadata_value_below_one_falls_back_to_default(self):
        scanner = _scanner(metadata_value=0)
        assert scanner._resolve_threshold("child-1") == 900

    def test_metadata_missing_returns_default(self):
        scanner = _scanner(metadata_value=None)
        assert scanner._resolve_threshold("child-1") == 900

    def test_metadata_non_int_string_falls_back(self):
        # SQLite TEXT round-trip — get_metadata_value re-parses JSON,
        # but a bare string value must NOT be honored.
        scanner = _scanner(metadata_value="900")
        assert scanner._resolve_threshold("child-1") == 900

    def test_metadata_bool_falls_back(self):
        # bool is an int subclass — the explicit bool trap.
        scanner = _scanner(metadata_value=True)
        assert scanner._resolve_threshold("child-1") == 900

    def test_default_below_one_rejected_by_ctor(self):
        with pytest.raises(ValueError):
            _scanner(default_threshold_seconds=0)

    def test_hard_max_above_24h_rejected_by_ctor(self):
        with pytest.raises(ValueError):
            _scanner(hard_max_threshold_seconds=86401)

    def test_default_above_hard_max_rejected_by_ctor(self):
        with pytest.raises(ValueError):
            _scanner(default_threshold_seconds=2000)

    def test_interval_zero_rejected_by_ctor(self):
        with pytest.raises(ValueError):
            _scanner(interval_seconds=0)


class TestResolveThresholdFloorBypass:
    """T11 — the read-side floor (AD-38): hand-edited below-floor
    metadata is invalid and falls back to the configured default."""

    def test_below_floor_30_falls_back_to_default(self):
        scanner = _scanner(metadata_value=30)
        assert scanner._resolve_threshold("child-1") == 900

    def test_floor_value_60_is_honored(self):
        scanner = _scanner(metadata_value=MIN_THRESHOLD_SECONDS)
        assert scanner._resolve_threshold("child-1") == MIN_THRESHOLD_SECONDS

    def test_zero_falls_back(self):
        scanner = _scanner(metadata_value=0)
        assert scanner._resolve_threshold("child-1") == 900

    def test_none_falls_back(self):
        scanner = _scanner(metadata_value=None)
        assert scanner._resolve_threshold("child-1") == 900

    def test_string_60_falls_back(self):
        scanner = _scanner(metadata_value="60")
        assert scanner._resolve_threshold("child-1") == 900

    def test_negative_falls_back(self):
        scanner = _scanner(metadata_value=-5)
        assert scanner._resolve_threshold("child-1") == 900

    def test_metadata_read_error_propagates_to_isolation(self):
        """A repo error during resolution is NOT silently absorbed —
        it propagates to run_once's per-instance error isolation
        (T3/U18 contract): that instance counts one tick error while
        the rest of the snapshot still scans."""
        scanner = _scanner()
        scanner._repo.get_metadata_value = MagicMock(
            side_effect=RuntimeError("db down")
        )
        with pytest.raises(RuntimeError, match="db down"):
            scanner._resolve_threshold("child-1")
