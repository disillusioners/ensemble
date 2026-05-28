"""Tests for timezone-aware timestamp behavior."""

from datetime import datetime, timezone
import pytest


def test_aware_isoformat_has_timezone_suffix():
    """Verify datetime.now(timezone.utc).isoformat() ends with +00:00."""
    aware_dt = datetime.now(timezone.utc)
    iso_str = aware_dt.isoformat()
    assert iso_str.endswith("+00:00"), f"Expected +00:00 suffix, got: {iso_str}"


def test_naive_isoformat_no_timezone_suffix():
    """Verify datetime.utcnow().isoformat() does NOT end with +00:00."""
    naive_dt = datetime.utcnow()
    iso_str = naive_dt.isoformat()
    assert not iso_str.endswith("+00:00"), f"Expected no +00:00 suffix, got: {iso_str}"


def test_aware_datetime_is_timezone_aware():
    """Verify datetime.now(timezone.utc).tzinfo is not None."""
    aware_dt = datetime.now(timezone.utc)
    assert aware_dt.tzinfo is not None, "Expected aware datetime to have tzinfo"


def test_naive_datetime_is_not_timezone_aware():
    """Verify datetime.utcnow().tzinfo is None."""
    naive_dt = datetime.utcnow()
    assert naive_dt.tzinfo is None, "Expected naive datetime to have no tzinfo"


def test_isoformat_parseable_by_standard_parser():
    """Verify datetime.fromisoformat() works and produces an aware datetime."""
    aware_dt = datetime.now(timezone.utc)
    iso_str = aware_dt.isoformat()
    parsed_dt = datetime.fromisoformat(iso_str)
    assert parsed_dt.tzinfo is not None, "Parsed datetime should be timezone-aware"
    # The parsed datetime should be equivalent to the original
    assert parsed_dt.replace(tzinfo=None) == aware_dt.replace(tzinfo=None)


def test_javascript_date_compatible():
    """Verify the format matches ISO 8601 with timezone (check for + or Z suffix)."""
    aware_dt = datetime.now(timezone.utc)
    iso_str = aware_dt.isoformat()
    # JavaScript Date.parse() and Date() accept ISO 8601 with +00:00 or Z
    # The + sign at the end indicates UTC (or the timezone offset)
    assert "+" in iso_str or iso_str.endswith("Z"), f"Expected + or Z suffix, got: {iso_str}"


def test_aware_and_naive_not_directly_comparable():
    """Demonstrate that comparing aware and naive datetimes raises TypeError."""
    aware_dt = datetime.now(timezone.utc)
    naive_dt = datetime.utcnow()
    with pytest.raises(TypeError):
        _ = aware_dt < naive_dt


def test_aware_utc_equals_naive_utc_in_value():
    """After replacing tzinfo with None, the values should be equal (within 1 second tolerance)."""
    aware_dt = datetime.now(timezone.utc)
    naive_dt = datetime.utcnow()
    # Strip timezone info from aware datetime
    naive_from_aware = aware_dt.replace(tzinfo=None)
    # They should be within 1 second of each other (same moment in time)
    diff = abs((naive_from_aware - naive_dt).total_seconds())
    assert diff < 1, f"Difference should be < 1 second, got {diff} seconds"
