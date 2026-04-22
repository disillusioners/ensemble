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

    def test_parse_utc_datetime_iso_with_timezone_offset(self):
        """ISO string with timezone offset should have tzinfo replaced with UTC.
        
        Note: The function uses .replace(tzinfo=utc) which replaces the timezone
        without converting the time value. So 10:30:00+05:30 becomes 10:30:00 UTC.
        """
        result = parse_utc_datetime("2024-01-15T10:30:00+05:30")
        expected = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        assert result == expected

    def test_parse_utc_datetime_date_only_string(self):
        """Date-only string (YYYY-MM-DD) should be parsed as midnight UTC."""
        result = parse_utc_datetime("2024-01-15")
        expected = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
        assert result == expected

    def test_parse_utc_datetime_invalid_string(self):
        """Invalid date string should raise ValueError."""
        with pytest.raises(ValueError):
            parse_utc_datetime("not-a-date")

    def test_parse_utc_datetime_malformed_string(self):
        """Malformed date string should raise ValueError."""
        with pytest.raises(ValueError):
            parse_utc_datetime("2024-13-45")  # Invalid month/day

    def test_parse_utc_datetime_negative_offset(self):
        """ISO string with negative timezone offset should have tzinfo replaced with UTC.
        
        Note: The function uses .replace(tzinfo=utc) which replaces the timezone
        without converting the time value. So 10:30:00-05:00 becomes 10:30:00 UTC.
        """
        result = parse_utc_datetime("2024-01-15T10:30:00-05:00")
        expected = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        assert result == expected

    def test_parse_utc_datetime_z_suffix(self):
        """ISO string with Z suffix should be parsed as UTC."""
        result = parse_utc_datetime("2024-01-15T10:30:00Z")
        expected = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        assert result == expected
