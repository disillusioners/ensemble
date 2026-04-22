"""Tests for daemon.utils utilities."""

from datetime import datetime, timezone

import pytest

from daemon.utils import parse_utc_datetime


class TestParseUtcDatetime:
    """Tests for parse_utc_datetime edge cases."""

    def test_parse_utc_datetime_none(self):
        """None input should return None."""
        assert parse_utc_datetime(None) is None

    def test_parse_utc_datetime_passthrough(self):
        """datetime object should pass through with UTC timezone."""
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = parse_utc_datetime(dt)
        assert result == dt
        assert result is dt  # same object

    def test_parse_utc_datetime_empty_string_raises(self):
        """Empty string should raise ValueError."""
        with pytest.raises(ValueError):
            parse_utc_datetime("")

    def test_parse_utc_datetime_iso_string(self):
        """ISO format string should be parsed and UTC-normalized."""
        result = parse_utc_datetime("2024-01-15T10:30:00")
        expected = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        assert result == expected

    def test_parse_utc_datetime_datetime_without_tz(self):
        """datetime without timezone should pass through unchanged."""
        dt = datetime(2024, 1, 15, 10, 30, 0)
        result = parse_utc_datetime(dt)
        # datetime objects pass through as-is (no UTC normalization added)
        assert result == dt
        assert result is dt
