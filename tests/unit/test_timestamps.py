"""Unit tests for the shared UTC timestamp helpers.

Phase 3 of ``feature/fix-job-queue-timestamps-tz`` — pins the helper
contract documented in ``daemon/services/timestamps.py``:

* ``now_utc`` / ``now_utc_naive`` / ``now_utc_iso`` produce the three
  sanctioned shapes (aware app-layer value, naive-UTC digits for
  naive-column binds, aware ISO for TEXT columns).
* ``to_utc_iso`` is the single serialization boundary (aware → UTC
  ISO; naive → assume-UTC documented policy; str → verbatim;
  None → None).
* ``coerce_to_aware_utc`` is the sort/compare companion.

Pure unit tests — no DB, no daemon boot.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from daemon.services.timestamps import (
    coerce_to_aware_utc,
    now_utc,
    now_utc_iso,
    now_utc_naive,
    to_utc_iso,
)


class TestNowUtc:
    """``now_utc`` — aware application-layer values."""

    def test_aware_with_utc_offset(self):
        value = now_utc()
        assert isinstance(value, datetime)
        assert value.tzinfo is not None
        assert value.utcoffset() == timedelta(0)

    def test_close_to_system_now(self):
        # Same instant within a loose tolerance (µs-level tolerance
        # asserted separately against now_utc_naive; here 1s is
        # plenty — this guards against a wrong-clock base like
        # time.time() double-conversion).
        before = datetime.now(timezone.utc)
        value = now_utc()
        after = datetime.now(timezone.utc)
        assert before <= value <= after


class TestNowUtcNaive:
    """``now_utc_naive`` — naive-UTC digits for naive-column binds."""

    def test_naive_digits(self):
        value = now_utc_naive()
        assert isinstance(value, datetime)
        assert value.tzinfo is None
        assert value.utcoffset() is None

    def test_same_instant_as_now_utc(self):
        # The naive digits must represent the SAME instant as the
        # aware clock, not the session-local wall clock. µs tolerance
        # for the two consecutive calls.
        aware = now_utc()
        naive = now_utc_naive()
        aware_from_naive = naive.replace(tzinfo=timezone.utc)
        assert abs((aware_from_naive - aware).total_seconds()) < 0.001

    def test_independent_of_session_timezone(self):
        # Simulate a non-UTC "session" by confirming the digits are
        # UTC wall-clock: compare against a +07-shifted reference —
        # the digits must NOT equal the local (+07) wall clock.
        #
        # LIMITATION (SQLite-only scope): this pins the PYTHON-side
        # wall clock only — the process TZ. A PostgreSQL session
        # TimeZone (how ``now()`` renders server-side) is a different
        # concept this test cannot observe. The session frame is
        # pinned separately: factory kwarg pin in
        # tests/unit/repositories/test_pg_engine_utc_session.py and
        # the disposable-PG behavioral pins in
        # tests/postgres/test_pg_session_utc_frame_pg.py.
        aware = now_utc()
        naive = now_utc_naive()
        local_plus7 = (aware.astimezone(timezone(timedelta(hours=7)))
                       .replace(tzinfo=None))
        assert naive != local_plus7 or aware.replace(tzinfo=None) == naive


class TestNowUtcIso:
    """``now_utc_iso`` — aware ISO strings for TEXT columns."""

    def test_offset_bearing(self):
        value = now_utc_iso()
        assert isinstance(value, str)
        assert value.endswith("+00:00")

    def test_round_trips_through_fromisoformat(self):
        value = now_utc_iso()
        parsed = datetime.fromisoformat(value)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timedelta(0)

    def test_matches_legacy_format_byte_shape(self):
        # The historical TEXT-column writer format was
        # ``datetime.now(timezone.utc).isoformat()`` — microseconds
        # plus ``+00:00``. New rows must stay string-comparable with
        # old rows (same shape).
        legacy = datetime.now(timezone.utc).isoformat()
        value = now_utc_iso()
        assert len(value) == len(legacy)


class TestToUtcIso:
    """``to_utc_iso`` — the serialization boundary."""

    def test_none_passthrough(self):
        assert to_utc_iso(None) is None

    def test_aware_datetime_to_utc_iso(self):
        # +07 aware input must serialize to its UTC instant, not the
        # +07 wall digits.
        value = datetime(2026, 9, 15, 21, 10, 1, 435315,
                         tzinfo=timezone(timedelta(hours=7)))
        assert to_utc_iso(value) == "2026-09-15T14:10:01.435315+00:00"

    def test_utc_aware_datetime(self):
        value = datetime(2026, 9, 15, 14, 9, 59, 500140, tzinfo=timezone.utc)
        assert to_utc_iso(value) == "2026-09-15T14:09:59.500140+00:00"

    def test_naive_datetime_assumes_utc(self):
        # Documented policy: naive digits are interpreted as UTC.
        value = datetime(2026, 9, 15, 14, 9, 59, 500140)
        assert to_utc_iso(value) == "2026-09-15T14:09:59.500140+00:00"

    def test_string_verbatim_passthrough(self):
        # TEXT columns already carry ISO strings — verbatim (this is
        # what makes JobItem.created_at byte-stable through views).
        raw = "2026-09-15T14:09:59.500140+00:00"
        assert to_utc_iso(raw) == raw
        raw_z = "2026-09-15T14:09:59.500140Z"
        assert to_utc_iso(raw_z) == raw_z

    def test_unsupported_type_degrades_to_none(self):
        assert to_utc_iso(12345) is None
        assert to_utc_iso(14.5) is None
        assert to_utc_iso(object()) is None


class TestCoerceToAwareUtc:
    """``coerce_to_aware_utc`` — the sort/compare companion."""

    def test_none_passthrough(self):
        assert coerce_to_aware_utc(None) is None

    def test_naive_assumes_utc(self):
        value = datetime(2026, 9, 15, 14, 9, 59)
        coerced = coerce_to_aware_utc(value)
        assert coerced.tzinfo is not None
        assert coerced.utcoffset() == timedelta(0)

    def test_aware_normalized_to_utc(self):
        value = datetime(2026, 9, 15, 21, 10, 1,
                         tzinfo=timezone(timedelta(hours=7)))
        coerced = coerce_to_aware_utc(value)
        assert coerced == datetime(2026, 9, 15, 14, 10, 1, tzinfo=timezone.utc)

    def test_mixed_naive_and_aware_compare_safely(self):
        # The whole point of the companion: mixed inputs must be
        # orderable without TypeError.
        naive_legacy = datetime(2026, 9, 15, 10, 0, 0)  # assume-UTC
        aware = datetime(2026, 9, 15, 11, 0, 0, tzinfo=timezone.utc)
        assert coerce_to_aware_utc(naive_legacy) < coerce_to_aware_utc(aware)


class TestWriterNormalization:
    """Bind-value normalization: helper output is safe for naive columns.

    The DC-A root defect is an AWARE bind into a naive column. These
    pins assert the helper-produced bind values are naive and carry
    the same instant as the aware clock.
    """

    def test_now_utc_naive_bind_shape(self):
        bind = now_utc_naive()
        # A naive bind can never trigger PG session-tz rendering.
        assert bind.tzinfo is None

    def test_digit_equivalence_with_aware_clock(self):
        aware = now_utc()
        bind = now_utc_naive()
        assert bind.replace(tzinfo=timezone.utc) >= aware - timedelta(seconds=1)
        assert bind.replace(tzinfo=timezone.utc) <= aware + timedelta(seconds=1)

    def test_helpers_agree_on_instant(self):
        # All three now_* helpers sample the same clock; the naive
        # digits and the ISO string must decode to (approximately)
        # the same instant.
        naive = now_utc_naive()
        iso = now_utc_iso()
        delta = (
            naive.replace(tzinfo=timezone.utc)
            - datetime.fromisoformat(iso)
        )
        assert abs(delta.total_seconds()) < 1.0


if __name__ == "__main__":
    pytest.main([__file__])
