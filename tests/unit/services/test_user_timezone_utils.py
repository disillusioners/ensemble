"""Unit tests for ``daemon.services.user_timezone_utils``.

Pure unit tests — mocks only, NO DB, NO daemon boot (safety fence mirrors
``tests/unit/services/test_scheduling_service.py``). The PG-backed HTTP
round-trips live in ``tests/test_settings_api.py`` (postgres-marked).

Contract pinned here (mirrors ``language_utils`` semantics but with an
UNSET sentinel of ``None`` — no default baked in):

* ``get_user_timezone_preference`` returns the stored IANA name, and
  ``None`` for: repo None / system project missing / no row / empty
  stored value / INVALID stored value (treat-as-unset, never crashes) /
  DB error (logged, never raises).
* ``current_utc_offset`` renders the zone's CURRENT offset (DST-correct
  at read time) as ``+07:00`` style, ``None`` on invalid names.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from daemon.services import user_timezone_utils as utz
from daemon.services.user_timezone_utils import (
    current_utc_offset,
    format_utc_offset,
    get_user_timezone_preference,
)


# ---------------------------------------------------------------------------
# Session seam (same technique as tests/test_settings_api.py helper tests —
# patch ``Session`` on the *user_timezone_utils* module, whose
# ``from sqlmodel import Session`` binding lives there).
# ---------------------------------------------------------------------------


class _DummySession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _repo_returning(meta_value) -> MagicMock:
    """A repo double whose ``get_metadata_record`` yields the given value."""
    repo = MagicMock()
    repo.engine = MagicMock()
    record = None if meta_value is None else SimpleNamespace(meta_value=meta_value)
    repo.get_metadata_record.return_value = record
    return repo


def _patch_session(monkeypatch):
    monkeypatch.setattr(utz, "Session", lambda *_a, **_kw: _DummySession())


# ---------------------------------------------------------------------------
# get_user_timezone_preference
# ---------------------------------------------------------------------------


class TestGetUserTimezonePreference:
    def test_returns_none_when_repo_is_none(self):
        """``None`` repo is handled — unset, NOT a default value."""
        assert get_user_timezone_preference(None) is None

    def test_returns_stored_value(self, monkeypatch):
        repo = _repo_returning("Asia/Bangkok")
        _patch_session(monkeypatch)
        assert get_user_timezone_preference(repo) == "Asia/Bangkok"

    def test_returns_none_when_metadata_record_missing(self, monkeypatch):
        repo = _repo_returning(None)
        _patch_session(monkeypatch)
        assert get_user_timezone_preference(repo) is None

    def test_returns_none_when_stored_value_empty(self, monkeypatch):
        repo = _repo_returning("")
        _patch_session(monkeypatch)
        assert get_user_timezone_preference(repo) is None

    def test_returns_none_when_system_project_missing(self, monkeypatch):
        """``SYSTEM_DEFAULT_PROJECT_ID`` unset → unset (no read attempted)."""
        from daemon import constants

        repo = _repo_returning("Asia/Bangkok")
        _patch_session(monkeypatch)
        original = constants.SYSTEM_DEFAULT_PROJECT_ID
        constants.SYSTEM_DEFAULT_PROJECT_ID = None
        try:
            assert get_user_timezone_preference(repo) is None
        finally:
            constants.SYSTEM_DEFAULT_PROJECT_ID = original
        repo.get_metadata_record.assert_not_called()

    def test_invalid_stored_value_treated_as_unset(self, monkeypatch):
        """A stored value that fails IANA validation reads as ``None`` — no crash."""
        repo = _repo_returning("Not/ARealZone")
        _patch_session(monkeypatch)
        assert get_user_timezone_preference(repo) is None

    def test_returns_none_when_repo_raises(self, monkeypatch):
        """A repo that raises during ``get_metadata_record`` → ``None``, no raise."""
        repo = MagicMock()
        repo.engine = MagicMock()
        repo.get_metadata_record.side_effect = RuntimeError("boom")
        _patch_session(monkeypatch)
        assert get_user_timezone_preference(repo) is None
        repo.get_metadata_record.assert_called_once()


# ---------------------------------------------------------------------------
# Offset display helpers
# ---------------------------------------------------------------------------


class TestFormatUtcOffset:
    def test_zero_offset(self):
        assert format_utc_offset(timedelta(0)) == "+00:00"

    def test_positive_whole_hour(self):
        assert format_utc_offset(timedelta(hours=7)) == "+07:00"

    def test_negative_offset(self):
        assert format_utc_offset(timedelta(hours=-5)) == "-05:00"

    def test_half_hour_offset(self):
        assert format_utc_offset(timedelta(hours=5, minutes=30)) == "+05:30"


class TestCurrentUtcOffset:
    def test_bangkok_is_plus_seven(self):
        """Asia/Bangkok has no DST — +07:00 year-round, deterministic."""
        assert current_utc_offset("Asia/Bangkok") == "+07:00"

    def test_utc_is_plus_zero_zero_zero(self):
        assert current_utc_offset("UTC") == "+00:00"

    def test_dst_correct_at_read_time(self):
        """America/New_York: July → EDT (-04:00), January → EST (-05:00).

        Both branches can be forced by comparing against the zone's own
        offset for 'now' — the helper must equal the zone's CURRENT
        offset whatever the host clock says, formatted with a colon.
        """
        from zoneinfo import ZoneInfo

        now = datetime.now(dt_timezone.utc)
        expected = now.astimezone(ZoneInfo("America/New_York")).strftime("%z")
        expected_colon = f"{expected[:-2]}:{expected[-2:]}"
        assert current_utc_offset("America/New_York") == expected_colon

    def test_invalid_name_returns_none(self):
        assert current_utc_offset("Not/ARealZone") is None

    def test_empty_name_returns_none(self):
        assert current_utc_offset("") is None
