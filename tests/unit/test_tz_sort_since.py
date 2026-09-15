"""Sort / since-filter correctness under mixed naive-UTC + aware values.

Phase 3 of ``feature/fix-job-queue-timestamps-tz`` (commit 8).

The writer conversion (commits 2-3) stores naive-UTC DIGITS in the
naive timestamp columns; the application layer keeps aware-UTC
values. Sorting and since-filtering must therefore handle BOTH
shapes in one comparison without ``TypeError`` and in TRUE
chronological order (not naive-vs-aware mixed order).

Pins:
* ``_normalize_sort_key`` (work_resolver) orders a page mixing naive
  Task datetimes (new naive-UTC digits) and aware JobItem parses.
* ``_parse_since`` / ``_parse_iso_for_compare`` (tools/missions)
  parse naive ISO strings as UTC (the documented assume-UTC policy)
  so a naive stored value compares correctly against an aware
  ``since`` bound.

Pure unit tests — no DB, no daemon boot.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from daemon.services.work_resolver import _normalize_sort_key
from daemon.tools.missions import _parse_iso_for_compare, _parse_since


def _naive_utc(h: int, m: int = 0) -> datetime:
    """A naive datetime holding UTC digits (the new storage shape)."""
    return datetime(2026, 9, 15, h, m, 0)


def _aware_utc(h: int, m: int = 0) -> datetime:
    return datetime(2026, 9, 15, h, m, 0, tzinfo=timezone.utc)


class TestNormalizeSortKeyMixedShapes:
    """``_normalize_sort_key`` — mixed naive(UTC-digits)+aware ordering."""

    def test_no_typeerror_on_mixed_page(self):
        # Python refuses naive-vs-aware comparison — the whole point
        # of the companion. A mixed page must sort, not raise.
        page = [_naive_utc(12), _aware_utc(10), _naive_utc(9), _aware_utc(14)]
        ordered = sorted(page, key=_normalize_sort_key)
        assert [v.hour for v in ordered] == [9, 10, 12, 14]

    def test_naive_assumed_utc_not_local(self):
        # A naive 12:00 (UTC digits) must sort BETWEEN aware 11:00 and
        # aware 13:00 — i.e. the digits are interpreted as UTC, not as
        # session-local (+07) which would place it 7h off.
        page = [_aware_utc(11), _naive_utc(12), _aware_utc(13)]
        ordered = sorted(page, key=_normalize_sort_key)
        assert ordered[1] is page[1]

    def test_none_sorts_oldest(self):
        page = [_aware_utc(10), None, _naive_utc(8)]
        ordered = sorted(page, key=_normalize_sort_key)
        assert ordered[0] is None or ordered[0] == _normalize_sort_key(None)
        # The None key is datetime.min-aware — strictly below both.
        key_none = _normalize_sort_key(None)
        assert key_none < _normalize_sort_key(_naive_utc(8))
        assert key_none < _normalize_sort_key(_aware_utc(10))

    def test_descending_newest_first(self):
        page = [_naive_utc(9), _aware_utc(15), _naive_utc(12)]
        ordered = sorted(page, key=_normalize_sort_key, reverse=True)
        assert [v.hour for v in ordered[:2]] == [15, 12]

    def test_aware_non_utc_normalized_to_utc(self):
        from datetime import timedelta as _td

        plus7_wall_19 = datetime(
            2026, 9, 15, 19, 0, 0, tzinfo=timezone(_td(hours=7))
        )
        # 19:00+07 wall == 12:00 UTC — must sort EQUAL to naive-UTC 12:00.
        assert _normalize_sort_key(plus7_wall_19) == _normalize_sort_key(_naive_utc(12))


class TestParseSinceAssumeUtc:
    """``_parse_since`` — naive since-bounds parse as UTC."""

    def test_naive_since_parsed_as_utc(self):
        parsed = _parse_since("2026-09-15T10:00:00")
        assert parsed is not None
        assert parsed.tzinfo is not None
        assert parsed.utcoffset().total_seconds() == 0
        assert parsed.hour == 10

    def test_aware_since_preserved(self):
        parsed = _parse_since("2026-09-15T10:00:00+00:00")
        assert parsed == _aware_utc(10)

    def test_z_suffix_accepted(self):
        parsed = _parse_since("2026-09-15T10:00:00Z")
        assert parsed == _aware_utc(10)

    def test_empty_and_garbage_degrade_to_none(self):
        assert _parse_since("") is None
        assert _parse_since(None) is None
        assert _parse_since("not-a-timestamp") is None


class TestParseIsoForCompareAssumeUtc:
    """``_parse_iso_for_compare`` — naive stored values compare as UTC."""

    def test_naive_value_assumed_utc(self):
        parsed = _parse_iso_for_compare("2026-09-15 14:09:59.500140")
        assert parsed is not None
        assert parsed.tzinfo is not None
        # The naive digits ARE the UTC wall clock.
        assert parsed == datetime(
            2026, 9, 15, 14, 9, 59, 500140, tzinfo=timezone.utc
        )

    def test_naive_vs_aware_comparison(self):
        # The since-filter composition: stored naive (assumed UTC)
        # must compare >= an earlier aware since-bound.
        stored = _parse_iso_for_compare("2026-09-15T12:00:00")
        since = _parse_since("2026-09-15T10:00:00+00:00")
        assert stored >= since

    def test_legacy_plus07_digits_read_7h_off_documented(self):
        # Documented policy trade-off: a LEGACY row carrying +07 wall
        # digits (pre-fix writers) is assumed-UTC and therefore
        # renders 7h EARLIER than its true instant until the backfill
        # repairs it. This pins the direction of the skew so the
        # backfill proposal can rely on it.
        legacy_digits = "2026-09-15 21:10:01.441308"  # true instant 14:10 UTC
        parsed = _parse_iso_for_compare(legacy_digits)
        assert parsed == datetime(
            2026, 9, 15, 21, 10, 1, 441308, tzinfo=timezone.utc
        )
        # i.e. 7h LATER than the true instant — assume-UTC on +07
        # digits moves the instant FORWARD by 7h. (Direction pinned;
        # magnitude owned by the backfill proposal.)
        assert (parsed - datetime(2026, 9, 15, 14, 10, 1, 441308,
                                  tzinfo=timezone.utc)).total_seconds() == 7 * 3600

    def test_none_and_garbage(self):
        assert _parse_iso_for_compare(None) is None
        assert _parse_iso_for_compare("") is None
        assert _parse_iso_for_compare("garbage") is None


if __name__ == "__main__":
    pytest.main([__file__])
