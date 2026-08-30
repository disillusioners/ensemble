"""Tests for daemon.utils utilities."""

import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

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


class TestInvokeAgentAndWaitRouting:
    """Routes ``invoke_agent_and_wait`` exceptions to readable refusal strings.

    Review finding from commit 7afde505's M3 hunk: the guard-predicate
    branch was placed inside ``except ValueError:`` and a bare ``raise``
    was used to forward non-guard ValueErrors to the sibling
    ``except Exception:``. Python does NOT offer a re-raised exception
    to sibling handlers — non-guard ValueErrors from
    ``spawn_instance_with_mcp`` ("Agent not found", "Max children limit
    reached", model allow-list errors) escaped the function entirely.
    Three of four call sites (knowledge_tools.py:841, chart_tools.py:101,
    image_tools.py:513) call without a wrapping try/except and expect
    the pre-batch graceful ``f"Error: {e}"`` contract.

    The fix collapses both handlers into one ``except Exception as e:``
    whose guard predicate scopes the readable-refusal branch; everything
    else flows through the original generic handler unchanged. These two
    tests pin both routes at the boundary.
    """

    def _make_manager(self, exc: Exception) -> MagicMock:
        """Build a MagicMock manager whose ``spawn_instance_with_mcp``
        raises ``exc`` on every call.
        """
        manager = MagicMock()
        manager.spawn_instance_with_mcp = AsyncMock(side_effect=exc)
        # Pre-empt the post-spawn cleanup branch if it ever runs.
        manager.terminate_instance = AsyncMock(return_value=None)
        return manager

    def _reset_invoke_semaphore(self, monkeypatch) -> None:
        """Replace the module-level singleton with a fresh ``Semaphore(1)``.

        The production singleton is lazily initialized and may already be
        held by other tests in the suite; a private fresh semaphore gives
        these tests an isolated, always-acquirable cap without touching
        the production cap.
        """
        from daemon import utils

        monkeypatch.setattr(utils, "_invoke_semaphore", asyncio.Semaphore(1))

    async def test_guard_refusal_returns_readable_string_no_escape(self, monkeypatch, caplog):
        """A guard-predicate ValueError surfaces a readable refusal string
        and does NOT escape the function.

        Predicate: ``isinstance(e, ValueError) and "Spawn refused" in msg
        and "governor" in msg.lower()`` (matches the lifecycle guard's
        byte-stable surface text — see ``daemon/tools/instance.py``
        ~:1852 and ~:2021).
        """
        from daemon import utils

        guard_msg = (
            "Spawn refused: parent chain already has 1 governor; refusing "
            "recursive spawn for governor agent_id=governor\n"
            "HINT: reuse existing children or terminate stale ones."
        )
        manager = self._make_manager(ValueError(guard_msg))
        self._reset_invoke_semaphore(monkeypatch)

        with caplog.at_level(logging.WARNING, logger="daemon.utils"):
            result = await utils.invoke_agent_and_wait(
                manager=manager,
                agent_id="governor",
                message="test prompt",
            )

        # Readable refusal string back; no exception escaped.
        assert isinstance(result, str)
        assert result.startswith("Error:")
        assert "Spawn refused" in result
        assert "governor" in result.lower()
        assert "HINT" in result
        # Guard-refusal log line MUST fire on this path.
        assert "spawn refused by guard" in caplog.text

    async def test_plain_value_error_returns_error_string_no_escape(self, monkeypatch, caplog):
        """A plain ValueError ("Agent not found") flows through the
        generic handler and returns ``f"Error: {e}"`` — does NOT escape,
        and is NOT treated/logged as a guard refusal.

        This is the route the old ``raise`` was supposed to take; the
        review found Python never offered it to the sibling handler.
        """
        from daemon import utils

        plain_msg = "Agent not found: worker_xyz"
        manager = self._make_manager(ValueError(plain_msg))
        self._reset_invoke_semaphore(monkeypatch)

        with caplog.at_level(logging.DEBUG, logger="daemon.utils"):
            result = await utils.invoke_agent_and_wait(
                manager=manager,
                agent_id="worker_xyz",
                message="test prompt",
            )

        # Graceful error string back; no exception escaped.
        assert isinstance(result, str)
        assert result.startswith("Error:")
        assert plain_msg in result
        # The fix must NOT route plain ValueErrors through the guard
        # branch — both the predicate and the log line must be absent.
        assert "spawn refused by guard" not in caplog.text
        # Generic error log line MUST fire on this path.
        assert "invoke_agent_and_wait failed" in caplog.text
