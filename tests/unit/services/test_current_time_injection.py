"""Pin tests for the user-timezone lines in the ``## Current Time`` section.

Commissioned pins (format was previously UNPINNED):

* **Unset shape** — ``append_current_time`` output is BYTE-IDENTICAL to the
  pre-timezone format when no user timezone is set (or the stored value is
  invalid at read time).
* **Set shape** — exactly two lines inserted between the ``Human:`` line and
  the ``time`` tool line, with the zone's CURRENT (DST-correct at render
  time) UTC offset, rendered in the user zone.

Byte-level assertions throughout: the full section is compared against a
literal string, so any accidental format drift fails loudly.

Pure unit tests — no DB, no daemon boot. The appender itself is DB-free by
design: the caller (``_apply_post_cache_appends``) resolves the preference
ONCE and passes it in; the wiring passthrough is pinned in
``TestPostCacheAppendsWiring``.
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from daemon.services import instance_lifecycle as lifecycle_mod
from daemon.services.instance_lifecycle import (
    _apply_post_cache_appends,
    append_current_time,
)

# A fixed UTC instant: Friday 2026-10-02 10:02:16Z (matches the shape the
# pre-existing Human line renders).
FIXED_NOW = datetime(2026, 10, 2, 10, 2, 16, tzinfo=dt_timezone.utc)

LEGACY_SECTION = (
    "\n---\n\n## Current Time\n\n"
    "ISO: 2026-10-02T10:02:16+00:00\n"
    "Human: Friday, 2026-10-02 10:02:16 UTC\n"
    "Use the `time` tool for fresh time information when needed."
)


# ---------------------------------------------------------------------------
# Unset shape — byte-identical to the pre-timezone format
# ---------------------------------------------------------------------------


class TestUnsetShapeByteIdentical:
    def test_unset_is_byte_identical_to_legacy_format(self):
        """``user_timezone=None`` → the section equals the legacy literal, byte for byte."""
        result = append_current_time("persona", now=FIXED_NOW)
        assert result == "persona" + LEGACY_SECTION

    def test_explicit_none_kwarg_is_byte_identical(self):
        """The keyword form used by the caller yields the same bytes."""
        result = append_current_time("persona", now=FIXED_NOW, user_timezone=None)
        assert result == "persona" + LEGACY_SECTION

    def test_invalid_timezone_treated_as_unset(self):
        """Invalid-at-read (garbage IANA name) → byte-identical, no crash."""
        result = append_current_time("persona", now=FIXED_NOW, user_timezone="Not/ARealZone")
        assert result == "persona" + LEGACY_SECTION

    def test_empty_string_timezone_treated_as_unset(self):
        result = append_current_time("persona", now=FIXED_NOW, user_timezone="")
        assert result == "persona" + LEGACY_SECTION


# ---------------------------------------------------------------------------
# Set shape — byte-level pins for the two inserted lines
# ---------------------------------------------------------------------------


class TestSetShapePins:
    def test_bangkok_exact_bytes(self):
        """The canonical set shape — literal byte comparison (no DST in Bangkok)."""
        expected = (
            "\n---\n\n## Current Time\n\n"
            "ISO: 2026-10-02T10:02:16+00:00\n"
            "Human: Friday, 2026-10-02 10:02:16 UTC\n"
            "User timezone: Asia/Bangkok (UTC+07:00)\n"
            "User local time: Friday, 2026-10-02 17:02:16 UTC+07:00 (Asia/Bangkok)\n"
            "Use the `time` tool for fresh time information when needed."
        )
        result = append_current_time("persona", now=FIXED_NOW, user_timezone="Asia/Bangkok")
        assert result == "persona" + expected

    def test_lines_sit_between_human_line_and_time_tool_line(self):
        result = append_current_time("persona", now=FIXED_NOW, user_timezone="Asia/Bangkok")
        human_idx = result.index("Human: Friday, 2026-10-02 10:02:16 UTC\n")
        tz_idx = result.index("User timezone: Asia/Bangkok (UTC+07:00)\n")
        local_idx = result.index("User local time: Friday, 2026-10-02 17:02:16 UTC+07:00 (Asia/Bangkok)\n")
        tool_idx = result.index("Use the `time` tool for fresh time information when needed.")
        assert human_idx < tz_idx < local_idx < tool_idx

    def test_utc_lines_verbatim_identical_between_set_and_unset(self):
        """The ISO + Human lines are untouched — ONLY the two lines are added."""
        unset = append_current_time("persona", now=FIXED_NOW)
        set_ = append_current_time("persona", now=FIXED_NOW, user_timezone="Asia/Bangkok")
        for line in ("ISO: 2026-10-02T10:02:16+00:00\n", "Human: Friday, 2026-10-02 10:02:16 UTC\n"):
            assert line in unset
            assert line in set_
        # Removing the two new lines from the set shape restores the unset
        # shape byte-for-byte (the UTC lines are verbatim, nothing reflowed).
        assert set_.replace(
            "User timezone: Asia/Bangkok (UTC+07:00)\n"
            "User local time: Friday, 2026-10-02 17:02:16 UTC+07:00 (Asia/Bangkok)\n",
            "",
        ) == unset

    def test_dst_correct_summer_offset(self):
        """America/New_York in July → EDT, UTC-04:00 (DST-correct at render)."""
        july = datetime(2026, 7, 15, 16, 0, 0, tzinfo=dt_timezone.utc)
        result = append_current_time("persona", now=july, user_timezone="America/New_York")
        assert "User timezone: America/New_York (UTC-04:00)" in result
        assert "User local time: Wednesday, 2026-07-15 12:00:00 UTC-04:00 (America/New_York)" in result

    def test_dst_correct_winter_offset(self):
        """America/New_York in January → EST, UTC-05:00 (standard time)."""
        january = datetime(2026, 1, 15, 16, 0, 0, tzinfo=dt_timezone.utc)
        result = append_current_time("persona", now=january, user_timezone="America/New_York")
        assert "User timezone: America/New_York (UTC-05:00)" in result
        assert "User local time: Thursday, 2026-01-15 11:00:00 UTC-05:00 (America/New_York)" in result

    def test_date_line_rollover(self):
        """18:30Z in Bangkok is already the NEXT day (Saturday) — local render rolls."""
        evening = datetime(2026, 10, 2, 18, 30, 0, tzinfo=dt_timezone.utc)
        result = append_current_time("persona", now=evening, user_timezone="Asia/Bangkok")
        assert "Human: Friday, 2026-10-02 18:30:00 UTC" in result
        assert "User local time: Saturday, 2026-10-03 01:30:00 UTC+07:00 (Asia/Bangkok)" in result

    def test_half_hour_zone_offset(self):
        """Kolkata (+05:30) — colon formatting keeps the minutes."""
        result = append_current_time("persona", now=FIXED_NOW, user_timezone="Asia/Kolkata")
        assert "User timezone: Asia/Kolkata (UTC+05:30)" in result
        assert "User local time: Friday, 2026-10-02 15:32:16 UTC+05:30 (Asia/Kolkata)" in result


# ---------------------------------------------------------------------------
# Wiring — _apply_post_cache_appends resolves the preference ONCE and passes
# it into the DB-free appender (the spawn :1946 / restore :4412 chain).
# ---------------------------------------------------------------------------


def _human_messages_agent_meta() -> SimpleNamespace:
    """AgentMeta stub mirroring tests/integration/test_context_injection_integration.py."""
    from daemon.registry import ContextInjectionConfig

    return SimpleNamespace(
        context_injection_mode="human_messages",
        context_injection=ContextInjectionConfig(heuristic_match_shared_md_files=True),
        skill_injection=True,
        allowed_models=None,
    )


class TestPostCacheAppendsWiring:
    def _run_appends(self, monkeypatch, stored_value):
        """Drive _apply_post_cache_appends with the preference stubbed at the seam."""
        monkeypatch.setattr(
            lifecycle_mod, "get_user_timezone_preference", lambda repo: stored_value
        )
        result, _language = _apply_post_cache_appends(
            system_prompt="base persona\n",
            instance_id="inst-tz",
            instance_repository=MagicMock(),
            shared_meta_kv_repo=None,
            parent_id=None,
            agent_id="agent-x",
            project_id=None,
            project_repository=MagicMock(),
            manager=MagicMock(),
            agent_meta=_human_messages_agent_meta(),
        )
        return result

    def test_set_preference_reaches_the_section(self, monkeypatch):
        result = self._run_appends(monkeypatch, "Asia/Bangkok")
        assert "User timezone: Asia/Bangkok (UTC+07:00)" in result
        assert "User local time: " in result
        assert "(Asia/Bangkok)" in result

    def test_unset_preference_keeps_legacy_shape(self, monkeypatch):
        result = self._run_appends(monkeypatch, None)
        assert "User timezone:" not in result
        assert "User local time:" not in result
        assert "## Current Time" in result

    def test_invalid_preference_keeps_legacy_shape(self, monkeypatch):
        result = self._run_appends(monkeypatch, "Not/ARealZone")
        assert "User timezone:" not in result
