"""Tests for chatbot platform context injection in instance lifecycle.

Tests cover ``append_platform_context()`` — the system-prompt appender that
injects platform-specific formatting rules (Discord / Slack / Telegram) into
ROOT instances spawned from a chat source adapter.

The appender is gated on two conditions:

1. ``parent_id is None`` — root of the instance tree (children skip).
2. ``source_type`` is a recognized platform key present in
   ``_PLATFORM_INSTRUCTIONS`` (unknown values silently skip).

The appender is intentionally pure: it receives ``source_type`` as a direct
parameter and does NOT query the DB. Mirrors ``append_user_language`` and
``append_current_time``.

These tests follow the pattern in ``tests/unit/test_context_key.py``.
"""

import pytest

from daemon.services.instance_lifecycle import (
    append_platform_context,
    _PLATFORM_INSTRUCTIONS,
)


# =============================================================================
# Helpers
# =============================================================================

BASE_PROMPT = "You are a helpful assistant."


# =============================================================================
# Part A: Recognized source_types
# =============================================================================

class TestRecognizedPlatforms:
    """A root instance with a recognized source_type gets platform context."""

    def test_discord_source_type(self):
        """discord source_type on a root → prompt contains Discord section."""
        result = append_platform_context(
            BASE_PROMPT, source_type="discord", parent_id=None
        )
        assert "Chat Platform: Discord" in result
        assert BASE_PROMPT in result

    def test_slack_source_type(self):
        """slack source_type on a root → prompt contains Slack section + mrkdwn."""
        result = append_platform_context(
            BASE_PROMPT, source_type="slack", parent_id=None
        )
        assert "Chat Platform: Slack" in result
        assert "mrkdwn" in result

    def test_telegram_source_type(self):
        """telegram source_type on a root → prompt contains Telegram section."""
        result = append_platform_context(
            BASE_PROMPT, source_type="telegram", parent_id=None
        )
        assert "Chat Platform: Telegram" in result

    def test_telegram_section_uses_markdownv2(self):
        """Telegram section must declare MarkdownV2 — bullets use markdown syntax."""
        section = _PLATFORM_INSTRUCTIONS["telegram"]
        assert "MarkdownV2" in section
        assert "HTML" not in section

    def test_exact_section_appended(self):
        """The appended text must exactly equal _PLATFORM_INSTRUCTIONS['discord']."""
        result = append_platform_context(
            BASE_PROMPT, source_type="discord", parent_id=None
        )
        assert result == BASE_PROMPT + _PLATFORM_INSTRUCTIONS["discord"]


# =============================================================================
# Part B: Gate / skip behavior
# =============================================================================

class TestGateBehavior:
    """Children, missing, and unknown source_types skip injection."""

    def test_child_instance_no_context(self):
        """parent_id set → prompt unchanged (no platform section)."""
        result = append_platform_context(
            BASE_PROMPT, source_type="discord", parent_id="some-parent"
        )
        assert result == BASE_PROMPT
        assert "Chat Platform" not in result

    def test_none_source_type_no_context(self):
        """source_type=None should not append any platform context."""
        result = append_platform_context(
            BASE_PROMPT, source_type=None, parent_id=None
        )
        assert result == BASE_PROMPT

    def test_missing_source_type_no_context(self):
        """source_type omitted (default None) → prompt unchanged."""
        result = append_platform_context(BASE_PROMPT, parent_id=None)
        assert result == BASE_PROMPT

    def test_unknown_source_type_no_context(self):
        """source_type not in whitelist (e.g. 'webhook') → prompt unchanged."""
        result = append_platform_context(
            BASE_PROMPT, source_type="webhook", parent_id=None
        )
        assert result == BASE_PROMPT

    def test_empty_string_source_type(self):
        """source_type is empty string → treated as missing, prompt unchanged."""
        result = append_platform_context(
            BASE_PROMPT, source_type="", parent_id=None
        )
        assert result == BASE_PROMPT


# =============================================================================
# Part C: Purity — no DB lookups
# =============================================================================

class TestPurity:
    """Appender is a pure function — no DB, no global state, no I/O."""

    def test_no_repo_required(self):
        """The appender accepts no repository argument — caller must
        pass ``source_type`` directly. This is the fix that prevents
        the silent no-op at spawn time (where the instance row has
        not been INSERTed yet)."""
        # Compiles only if the new signature has no repository param.
        import inspect

        sig = inspect.signature(append_platform_context)
        assert "instance_repository" not in sig.parameters
        assert "instance_id" not in sig.parameters
        assert "source_type" in sig.parameters
        assert "parent_id" in sig.parameters

    def test_pure_returns_prompt_unchanged_for_unknown(self):
        """No DB, no mock — the appender takes a string and returns a string."""
        result = append_platform_context(
            BASE_PROMPT, source_type="definitely-not-a-platform", parent_id=None
        )
        assert result == BASE_PROMPT
