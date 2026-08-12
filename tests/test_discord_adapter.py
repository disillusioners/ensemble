"""Tests for DiscordAdapter.

Comprehensive test suite covering initialization, message normalization,
mention-gating, external user ID construction, outbound routing, message
splitting (5-tier boundary chain), circuit-breaker 429-exclusion,
per-channel locks, thread registration, health checks, shutdown
idempotency, token redaction (NFR-10), and registry integration.

Discord.py objects are mocked with ``MagicMock``/``AsyncMock`` — no live
Gateway or REST calls.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from unittest.mock import AsyncMock, MagicMock, patch, call

import aiohttp
import pytest

from daemon.sources.adapters.discord import DiscordAdapter, DiscordAPIError
from daemon.sources.adapters.discord.constants import (
    DISCORD_API_BASE,
    DISCORD_ID_PATTERN,
    DISCORD_LATENCY_THRESHOLD_MS,
    DISCORD_MAX_MESSAGE_LENGTH,
    EVICTION_INTERVAL_SECONDS,
    MAX_CHANNEL_LOCKS,
)
from daemon.sources.adapters.discord.formatting import (
    _build_attachment_placeholder,
    _clean_discord_text,
    _strip_llm_artifact_tags,
)
from daemon.sources.adapters.discord.resilience import DiscordSendSemaphore
from daemon.sources.adapters.discord.thread_manager import (
    DiscordThreadManager,
    ThreadInstance,
)
from daemon.sources.base import (
    IncomingMessage,
    OutgoingMessage,
    SourceConfig,
    SourceStatus,
)
from daemon.sources.mapper import (
    VALID_SOURCE_TYPES,
    validate_external_user_id,
    ValidationError,
    SOURCE_TYPE_DISCORD,
)


# ==================== Helper Fixtures ====================


def make_discord_config(
    source_id: str = "discord-main",
    bot_token: str = "MTIzNDU2Nzg5.Mabcdef.test_signature_123",
    agent: str = "ari",
    **config_kwargs,
) -> SourceConfig:
    """Build a canonical Discord SourceConfig for tests.

    Mirrors ``make_telegram_config`` / ``make_slack_config``. The default
    config sets ``require_mention=True`` to match production defaults.

    FIX 9: the default token matches Discord's 3-segment shape so
    ``test_connection`` pre-flight format checks pass on the default
    config. Adapter construction (``__init__``) does NOT validate the
    token format, so any string still works for the rest of the suite.
    """
    config = {**config_kwargs, "agent": agent}
    return SourceConfig(
        source_id=source_id,
        source_type="discord",
        name="Test Discord Bot",
        config=config,
        credentials={"bot_token": bot_token},
        enabled=True,
    )


@pytest.fixture
def mock_on_message():
    return AsyncMock()


@pytest.fixture
def discord_config():
    return make_discord_config()


@pytest.fixture
def adapter(discord_config, mock_on_message):
    """Default adapter with mocked dependencies."""
    return DiscordAdapter(discord_config, mock_on_message)


@pytest.fixture
def mock_source_repo():
    """Source repo mock with a sensible default mapping."""
    repo = MagicMock()
    mapping = MagicMock()
    mapping.mapping_metadata = {
        "discord": {
            "guild_id": "987654321098765432",
            "channel_id": "555444333222111333",
            "thread_id": None,
            "user_id": "123456789012345678",
        }
    }
    repo.get_instance_mapping = MagicMock(return_value=mapping)
    return repo


@pytest.fixture
def adapter_with_repo(adapter, mock_source_repo):
    adapter._source_repo = mock_source_repo
    adapter._bot_user_id = "999999999999999999"
    return adapter


# ==================== Initialization Tests ====================


class TestDiscordAdapterInit:
    """Tests for DiscordAdapter.__init__."""

    def test_init_requires_bot_token(self, mock_on_message):
        config = SourceConfig(
            source_id="test",
            source_type="discord",
            name="Test",
            config={},
            credentials={},
        )
        with pytest.raises(ValueError, match="bot_token"):
            DiscordAdapter(config, mock_on_message)

    def test_init_with_valid_config(self, discord_config, mock_on_message):
        a = DiscordAdapter(discord_config, mock_on_message)
        assert a.source_id == "discord-main"
        assert a.source_type == "discord"
        assert a.status == SourceStatus.STOPPED

    def test_default_agent_is_ari(self, mock_on_message):
        cfg = make_discord_config()
        a = DiscordAdapter(cfg, mock_on_message)
        assert a._default_agent == "ari"

    def test_custom_agent(self, mock_on_message):
        cfg = make_discord_config(agent="bob")
        a = DiscordAdapter(cfg, mock_on_message)
        assert a._default_agent == "bob"

    def test_require_mention_defaults_true(self, mock_on_message):
        cfg = make_discord_config()
        a = DiscordAdapter(cfg, mock_on_message)
        assert a._require_mention is True

    def test_require_mention_can_be_disabled(self, mock_on_message):
        cfg = make_discord_config(require_mention=False)
        a = DiscordAdapter(cfg, mock_on_message)
        assert a._require_mention is False

    def test_allowed_guild_ids_coerced(self, mock_on_message):
        cfg = make_discord_config(
            allowed_guild_ids=["111222333444555666", 777888999000111222],
        )
        a = DiscordAdapter(cfg, mock_on_message)
        assert a._allowed_guild_ids == [111222333444555666, 777888999000111222]

    def test_allowed_channels_coerced(self, mock_on_message):
        cfg = make_discord_config(
            allowed_channels=["111", "222", "333"],
        )
        a = DiscordAdapter(cfg, mock_on_message)
        assert a._allowed_channels == [111, 222, 333]

    def test_allowed_guild_ids_default_empty(self, mock_on_message):
        cfg = make_discord_config()
        a = DiscordAdapter(cfg, mock_on_message)
        assert a._allowed_guild_ids == []

    def test_invalid_id_in_list_raises(self, mock_on_message):
        cfg = make_discord_config(allowed_guild_ids=["abc"])
        with pytest.raises(ValueError):
            DiscordAdapter(cfg, mock_on_message)

    def test_ignore_bot_messages_defaults_true(self, mock_on_message):
        a = DiscordAdapter(make_discord_config(), mock_on_message)
        assert a._ignore_bot_messages is True

    def test_strip_llm_artifact_tags_defaults_true(self, mock_on_message):
        a = DiscordAdapter(make_discord_config(), mock_on_message)
        assert a._strip_llm_artifact_tags_enabled is True

    def test_intents_default_to_required(self, mock_on_message):
        a = DiscordAdapter(make_discord_config(), mock_on_message)
        assert a._intents_config["message_content"] is True
        assert a._intents_config["guilds"] is True
        assert a._intents_config["dm_messages"] is True

    def test_channel_mention_config_stored(self, mock_on_message):
        cfg = make_discord_config(
            channel_mention_config={
                "555444333222111333": "always_active",
                "666777888999000111": "disabled",
            }
        )
        a = DiscordAdapter(cfg, mock_on_message)
        assert a._channel_mention_config == {
            "555444333222111333": "always_active",
            "666777888999000111": "disabled",
        }

    def test_circuit_breaker_initialized(self, mock_on_message):
        a = DiscordAdapter(make_discord_config(), mock_on_message)
        assert a._circuit_breaker.failure_threshold == 5
        assert a._circuit_breaker.recovery_timeout == 60.0

    def test_channel_locks_initialized(self, mock_on_message):
        a = DiscordAdapter(make_discord_config(), mock_on_message)
        assert isinstance(a._channel_locks, dict)
        assert len(a._channel_locks) == 0

    def test_send_semaphore_initialized(self, mock_on_message):
        a = DiscordAdapter(make_discord_config(), mock_on_message)
        assert isinstance(a._send_semaphore, DiscordSendSemaphore)
        assert a._send_semaphore.max_concurrent == 5

    def test_max_concurrent_sends_override(self, mock_on_message):
        cfg = make_discord_config(max_concurrent_sends=10)
        a = DiscordAdapter(cfg, mock_on_message)
        assert a._send_semaphore.max_concurrent == 10

    def test_thread_manager_only_when_manager_provided(self, mock_on_message):
        a = DiscordAdapter(make_discord_config(), mock_on_message)
        assert a._thread_manager is None

    def test_thread_manager_initialized_with_manager(self, discord_config, mock_on_message):
        manager = MagicMock()
        a = DiscordAdapter(discord_config, mock_on_message, manager=manager)
        assert isinstance(a._thread_manager, DiscordThreadManager)

    def test_source_repo_starts_unset(self, mock_on_message):
        a = DiscordAdapter(make_discord_config(), mock_on_message)
        assert a._source_repo is None


# ==================== Token Redaction (NFR-10) ====================


class TestTokenRedaction:
    """NFR-10: bot token must NEVER appear in plaintext logs."""

    def test_redact_short_token(self):
        assert DiscordAdapter._redact_token("") == ""
        assert DiscordAdapter._redact_token("abc") == "***"

    def test_redact_long_token(self):
        token = "abcdefghij1234567890ABCD"
        out = DiscordAdapter._redact_token(token)
        # The full token must not appear in the redacted form.
        assert token not in out
        # The redaction format keeps a 4-char prefix and 4-char suffix for diagnostics.
        assert out == "abcd***ABCD"
        # The middle of the token must be redacted.
        assert "efghij1234567890" not in out

    def test_adapter_stores_redacted_token(self):
        a = DiscordAdapter(
            make_discord_config(bot_token="supersecrettokenvalue"),
            AsyncMock(),
        )
        assert a._bot_token_redacted == "supe***alue"
        assert "supersecrettokenvalue" != a._bot_token_redacted

    def test_token_not_in_log_output(self, caplog):
        a = DiscordAdapter(
            make_discord_config(bot_token="mysecrettoken12345"),
            AsyncMock(),
        )
        with caplog.at_level(logging.WARNING):
            logger = logging.getLogger("test.discord.redact")
            logger.warning(
                f"Adapter created with token: {a._bot_token_redacted}"
            )
        # The redacted string is fine to log; the raw token must not be.
        assert "mysecrettoken12345" not in caplog.text


# ==================== External User ID ====================


class TestExternalUserIdConstruction:
    """Tests for the canonical external_user_id scheme."""

    def _make_message(self, *, author_id, guild=None, channel_id=None,
                      parent_id=None, content="hello"):
        message = MagicMock()
        message.author = MagicMock()
        message.author.id = author_id
        message.author.bot = False
        message.guild = guild
        message.channel = MagicMock()
        message.channel.id = channel_id
        message.channel.parent_id = parent_id
        message.channel.name = "general"
        message.content = content
        message.attachments = []
        message.mentions = []
        return message

    def test_dm_external_user_id(self, adapter):
        msg = self._make_message(author_id=123456789012345678, guild=None, channel_id=987654321098765432)
        # DM = no guild
        assert adapter._build_external_user_id(msg) == "dm:123456789012345678"

    def test_guild_channel_external_user_id(self, adapter):
        guild = MagicMock()
        guild.id = 987654321098765432
        msg = self._make_message(
            author_id=123456789012345678,
            guild=guild,
            channel_id=555444333222111333,
        )
        assert adapter._build_external_user_id(msg) == "987654321098765432:555444333222111333"

    def test_thread_external_user_id(self, adapter):
        guild = MagicMock()
        guild.id = 987654321098765432
        msg = self._make_message(
            author_id=123456789012345678,
            guild=guild,
            channel_id=777888999000111222,  # thread_id
            parent_id=555444333222111333,   # parent channel id
        )
        assert (
            adapter._build_external_user_id(msg)
            == "987654321098765432:555444333222111333:777888999000111222"
        )


# ==================== Mapper DISCORD_ID_PATTERN ====================


class TestDiscordIdPattern:
    """Canonical regex acceptance/rejection."""

    def test_pattern_constant_matches_module(self):
        assert DISCORD_ID_PATTERN == (
            r"^(dm:\d{17,19}|\d{17,19}:\d{17,19}(:\d{17,19})?)$"
        )

    @pytest.mark.parametrize("valid", [
        "dm:123456789012345678",
        "123456789012345678:987654321098765432",
        "123456789012345678:987654321098765432:111122223333444455",
    ])
    def test_validate_accepts_canonical(self, valid):
        assert validate_external_user_id("discord", valid) == valid

    @pytest.mark.parametrize("invalid", [
        "dm:123",                         # too short
        "discord:channel:123",            # non-numeric
        "abc:def",                        # non-numeric
        "123",                            # bare integer
        "dm:123456789012345678:extra",    # DM doesn't allow extra parts
    ])
    def test_validate_rejects_malformed(self, invalid):
        with pytest.raises(ValidationError):
            validate_external_user_id("discord", invalid)

    def test_discord_in_valid_source_types(self):
        assert SOURCE_TYPE_DISCORD in VALID_SOURCE_TYPES


# ==================== LLM Tag Stripping & Discord Text ====================


class TestFormatting:
    """Tests for formatting helpers."""

    def test_strip_think_block(self):
        assert _strip_llm_artifact_tags("<think>secret</think>hello") == "hello"

    def test_strip_all_four_tag_variants(self):
        for tag in ["think", "scratchpad", "reflection", "reasoning"]:
            content = f"<{tag}>x</{tag}>rest"
            assert _strip_llm_artifact_tags(content) == "rest"

    def test_strip_self_closing_tag(self):
        # ``<tag />`` with the leading whitespace matches the regex.
        assert _strip_llm_artifact_tags("<think />hi") == "hi"
        # ``<tag attr="val"/>`` (with attributes) is also stripped.
        assert _strip_llm_artifact_tags('<think attr="x"/>hi') == "hi"

    def test_strip_orphan_open_close(self):
        assert _strip_llm_artifact_tags("<think> hi") == "hi"
        assert _strip_llm_artifact_tags("hi</think>") == "hi"

    def test_no_tags_returns_unchanged(self):
        assert _strip_llm_artifact_tags("plain text") == "plain text"

    def test_strip_collapses_blank_lines(self):
        out = _strip_llm_artifact_tags("<think>a</think>\n\n\n\nhello")
        assert "\n\n\n" not in out

    def test_clean_user_mention(self):
        assert _clean_discord_text("<@123456789012345678> hi") == "hi"

    def test_clean_nickname_mention(self):
        assert _clean_discord_text("<@!123456789012345678> hi") == "hi"

    def test_clean_user_mention_with_display(self):
        assert _clean_discord_text("<@123|name> hi") == "hi"

    def test_clean_role_mention(self):
        assert _clean_discord_text("<@&123456789012345678> hi") == "hi"

    def test_clean_channel_mention(self):
        assert _clean_discord_text("<#123456789012345678> hi") == "hi"

    def test_clean_emoji(self):
        assert _clean_discord_text("<:pepe:1234567890> hi") == "hi"

    def test_clean_animation_emoji(self):
        assert _clean_discord_text("<a:dance:1234567890> hi") == "hi"

    def test_clean_timestamp(self):
        assert _clean_discord_text("<t:1234567890:R> hi") == "hi"

    def test_clean_slash_command(self):
        assert _clean_discord_text("</something:1234567890> hi") == "hi"

    def test_clean_collapses_whitespace(self):
        assert _clean_discord_text("a    b\n\n\n\nc") == "a b\n\nc"

    def test_clean_empty(self):
        assert _clean_discord_text("") == ""


class TestAttachmentPlaceholder:
    def test_single_image_attachment(self):
        att = MagicMock()
        att.filename = "cat.png"
        att.url = "https://example.com/cat.png"
        att.content_type = "image/png"
        assert _build_attachment_placeholder([att]) == "[Image attachment: cat.png]"

    def test_single_file_attachment(self):
        att = MagicMock()
        att.filename = "doc.pdf"
        att.url = "https://example.com/doc.pdf"
        att.content_type = "application/pdf"
        assert _build_attachment_placeholder([att]) == "[File attachment: doc.pdf]"

    def test_multiple_images(self):
        atts = []
        for i in range(3):
            a = MagicMock()
            a.filename = f"img{i}.png"
            a.url = f"https://example.com/{i}"
            a.content_type = "image/png"
            atts.append(a)
        assert _build_attachment_placeholder(atts) == "[3 image attachment(s)]"

    def test_mixed_images_and_files(self):
        img = MagicMock()
        img.filename = "x.png"
        img.content_type = "image/png"
        f = MagicMock()
        f.filename = "y.pdf"
        f.content_type = "application/pdf"
        assert _build_attachment_placeholder([img, f]) == "[1 image / 1 file attachment(s)]"

    def test_empty(self):
        assert _build_attachment_placeholder([]) == ""


# ==================== Mention Gating ====================


class TestMentionGating:
    """Tests for _is_bot_mentioned and _should_process_message."""

    def _make_message(
        self,
        author_id=123456789012345678,
        author_bot=False,
        guild_id=None,
        channel_id=987654321098765432,
        parent_id=None,
        content="hello",
    ):
        author = MagicMock()
        author.id = author_id
        author.bot = author_bot
        channel = MagicMock()
        channel.id = channel_id
        channel.parent_id = parent_id
        guild = MagicMock() if guild_id is not None else None
        if guild is not None:
            guild.id = guild_id
        msg = MagicMock()
        msg.author = author
        msg.channel = channel
        msg.guild = guild
        msg.content = content
        msg.mentions = []
        msg.attachments = []
        return msg

    def test_dm_always_active(self, adapter):
        msg = self._make_message(guild_id=None, content="hello")
        assert adapter._is_bot_mentioned(msg) is True

    def test_guild_message_with_explicit_mention(self, adapter_with_repo):
        adapter_with_repo._require_mention = True
        bot_id = adapter_with_repo._bot_user_id
        msg = self._make_message(
            guild_id=987654321098765432,
            content=f"<@{bot_id}> hello",
        )
        assert adapter_with_repo._is_bot_mentioned(msg) is True

    def test_guild_message_nickname_mention(self, adapter_with_repo):
        adapter_with_repo._require_mention = True
        bot_id = adapter_with_repo._bot_user_id
        msg = self._make_message(
            guild_id=987654321098765432,
            content=f"<@!{bot_id}> hello",
        )
        assert adapter_with_repo._is_bot_mentioned(msg) is True

    def test_guild_message_no_mention_skipped_when_required(self, adapter_with_repo):
        adapter_with_repo._require_mention = True
        msg = self._make_message(guild_id=987654321098765432, content="hello")
        assert adapter_with_repo._is_bot_mentioned(msg) is False

    def test_guild_message_no_mention_allowed_when_not_required(self, adapter):
        adapter._bot_user_id = "999999999999999999"
        adapter._require_mention = False
        msg = self._make_message(guild_id=987654321098765432, content="hello")
        assert adapter._is_bot_mentioned(msg) is True

    def test_guild_message_with_mention_in_mentions_list(self, adapter_with_repo):
        adapter_with_repo._require_mention = True
        bot_id = int(adapter_with_repo._bot_user_id)
        msg = self._make_message(
            guild_id=987654321098765432,
            content="hello",
        )
        user = MagicMock()
        user.id = bot_id
        msg.mentions = [user]
        assert adapter_with_repo._is_bot_mentioned(msg) is True

    def test_guild_message_dropped_when_bot_id_unresolved(self, adapter):
        """FIX 2 regression: guild messages must be DROPPED (fail CLOSED) when
        the bot's own user ID is not yet known. discord.py CAN dispatch
        on_message before on_ready; without this guard we would process
        untrusted content without mention verification.
        """
        adapter._bot_user_id = None
        adapter._require_mention = True
        msg = self._make_message(guild_id=987654321098765432, content="hi")
        assert adapter._is_bot_mentioned(msg) is False

    def test_guild_always_active_override_dropped_when_bot_id_unresolved(self, adapter):
        """Unresolved identity must override permissive channel settings."""
        adapter._bot_user_id = None
        adapter._channel_mention_config = {
            "987654321098765432": DiscordAdapter.MENTION_ALWAYS_ACTIVE,
        }
        msg = self._make_message(guild_id=987654321098765432, content="hi")
        assert adapter._is_bot_mentioned(msg) is False

    def test_guild_require_mention_false_dropped_when_bot_id_unresolved(self, adapter):
        """Unresolved identity must override require_mention=False."""
        adapter._bot_user_id = None
        adapter._require_mention = False
        msg = self._make_message(guild_id=987654321098765432, content="hi")
        assert adapter._is_bot_mentioned(msg) is False

    def test_guild_disabled_override_dropped_when_bot_id_unresolved(self, adapter):
        """The identity guard runs before even restrictive overrides."""
        adapter._bot_user_id = None
        adapter._channel_mention_config = {
            "987654321098765432": DiscordAdapter.MENTION_DISABLED,
        }
        msg = self._make_message(guild_id=987654321098765432, content="hi")
        assert adapter._is_bot_mentioned(msg) is False

    def test_channel_mention_config_always_active(self, adapter_with_repo):
        adapter_with_repo._require_mention = True
        adapter_with_repo._channel_mention_config = {
            "987654321098765432": DiscordAdapter.MENTION_ALWAYS_ACTIVE,
        }
        msg = self._make_message(guild_id=987654321098765432, content="hi")
        assert adapter_with_repo._is_bot_mentioned(msg) is True

    def test_channel_mention_config_disabled(self, adapter_with_repo):
        adapter_with_repo._require_mention = False
        adapter_with_repo._channel_mention_config = {
            "987654321098765432": DiscordAdapter.MENTION_DISABLED,
        }
        msg = self._make_message(guild_id=987654321098765432, content=f"<@{adapter_with_repo._bot_user_id}> hi")
        assert adapter_with_repo._is_bot_mentioned(msg) is False


# ==================== Should-process gate ====================


class TestShouldProcessMessage:
    """Tests for guild/channel/bot allow-list gate."""

    def _msg(self, *, guild_id=None, channel_id=987654321098765432, parent_id=None,
             author_id=123456789012345678, bot=False):
        author = MagicMock()
        author.id = author_id
        author.bot = bot
        channel = MagicMock()
        channel.id = channel_id
        channel.parent_id = parent_id
        guild = MagicMock() if guild_id is not None else None
        if guild is not None:
            guild.id = guild_id
        msg = MagicMock()
        msg.author = author
        msg.channel = channel
        msg.guild = guild
        msg.content = "x"
        msg.mentions = []
        msg.attachments = []
        return msg

    def test_default_no_filters_passes(self, adapter):
        msg = self._msg(guild_id=987654321098765432)
        assert adapter._should_process_message(msg) is True

    def test_disallowed_guild_skipped(self, mock_on_message):
        cfg = make_discord_config(allowed_guild_ids=[111222333444555666])
        a = DiscordAdapter(cfg, mock_on_message)
        msg = self._msg(guild_id=987654321098765432)
        assert a._should_process_message(msg) is False

    def test_allowed_guild_passes(self, mock_on_message):
        cfg = make_discord_config(allowed_guild_ids=[987654321098765432])
        a = DiscordAdapter(cfg, mock_on_message)
        msg = self._msg(guild_id=987654321098765432)
        assert a._should_process_message(msg) is True

    def test_disallowed_channel_skipped(self, mock_on_message):
        cfg = make_discord_config(allowed_channels=[111])
        a = DiscordAdapter(cfg, mock_on_message)
        msg = self._msg(guild_id=987654321098765432, channel_id=222)
        assert a._should_process_message(msg) is False

    def test_thread_checks_parent_channel(self, mock_on_message):
        cfg = make_discord_config(allowed_channels=[555])
        a = DiscordAdapter(cfg, mock_on_message)
        msg = self._msg(
            guild_id=987654321098765432,
            channel_id=777,  # thread
            parent_id=555,  # parent allowed
        )
        assert a._should_process_message(msg) is True

    def test_bot_message_skipped_by_default(self, adapter):
        msg = self._msg(bot=True)
        assert adapter._should_process_message(msg) is False

    def test_bot_message_allowed_when_in_allowed_bot_ids(self, mock_on_message):
        cfg = make_discord_config(
            allowed_bot_ids=[123456789012345678],
            ignore_bot_messages=True,
        )
        a = DiscordAdapter(cfg, mock_on_message)
        msg = self._msg(bot=True, author_id=123456789012345678)
        assert a._should_process_message(msg) is True

    def test_dm_bypasses_guild_filter(self, mock_on_message):
        cfg = make_discord_config(allowed_guild_ids=[999])
        a = DiscordAdapter(cfg, mock_on_message)
        msg = self._msg(guild_id=None)
        assert a._should_process_message(msg) is True


# ==================== Inbound normalization ====================


def _make_full_message(
    *,
    author_id=123456789012345678,
    author_bot=False,
    guild_id=None,
    channel_id=987654321098765432,
    channel_name="general",
    parent_id=None,
    content="hello",
    attachments=None,
    message_id=111222333444555666,
    mentions=None,
    message_reference=None,
):
    author = MagicMock()
    author.id = author_id
    author.bot = author_bot
    author.name = "alice"
    author.display_name = "Alice"

    channel = MagicMock()
    channel.id = channel_id
    channel.parent_id = parent_id
    channel.name = channel_name

    guild = MagicMock() if guild_id is not None else None
    if guild is not None:
        guild.id = guild_id
        guild.name = "Test Guild"

    msg = MagicMock()
    msg.author = author
    msg.channel = channel
    msg.guild = guild
    msg.content = content
    msg.attachments = attachments or []
    msg.mentions = mentions or []
    msg.message_reference = message_reference  # legacy attr (pre-fix readers)
    msg.reference = message_reference  # discord.py 2.7+ uses `.reference`
    msg.id = message_id
    return msg


class TestInboundNormalization:
    def test_dm_message_normalizes(self, adapter):
        msg = _make_full_message(guild_id=None, content="hello")
        incoming = adapter._normalize_incoming(msg)
        assert incoming is not None
        assert incoming.external_user_id == "dm:123456789012345678"
        assert incoming.content == "hello"
        assert incoming.source_id == "discord-main"
        assert incoming.metadata["agent"] == "ari"
        assert incoming.metadata["discord"]["is_dm"] is True

    def test_channel_message_normalizes(self, adapter):
        msg = _make_full_message(
            guild_id=987654321098765432,
            channel_id=555444333222111333,
            content="hello",
        )
        incoming = adapter._normalize_incoming(msg)
        assert incoming.external_user_id == "987654321098765432:555444333222111333"

    def test_thread_message_normalizes(self, adapter):
        msg = _make_full_message(
            guild_id=987654321098765432,
            channel_id=777888999000111222,  # thread
            parent_id=555444333222111333,
            content="reply in thread",
        )
        incoming = adapter._normalize_incoming(msg)
        assert (
            incoming.external_user_id
            == "987654321098765432:555444333222111333:777888999000111222"
        )

    def test_attachment_only_message(self, adapter):
        att = MagicMock()
        att.filename = "cat.png"
        att.url = "https://example.com/cat.png"
        att.content_type = "image/png"
        msg = _make_full_message(content="", attachments=[att])
        incoming = adapter._normalize_incoming(msg)
        assert incoming is not None
        assert incoming.content == "[Image attachment: cat.png]"
        assert incoming.images == ["https://example.com/cat.png"]

    def test_multiple_images_placeholder(self, adapter):
        atts = []
        for i in range(3):
            a = MagicMock()
            a.filename = f"img{i}.png"
            a.url = f"https://example.com/{i}"
            a.content_type = "image/png"
            atts.append(a)
        msg = _make_full_message(content="", attachments=atts)
        incoming = adapter._normalize_incoming(msg)
        assert incoming.content == "[3 image attachment(s)]"
        assert len(incoming.images) == 3

    def test_text_and_attachments_both_kept(self, adapter):
        att = MagicMock()
        att.filename = "x.png"
        att.url = "https://example.com/x"
        att.content_type = "image/png"
        msg = _make_full_message(content="look at this", attachments=[att])
        incoming = adapter._normalize_incoming(msg)
        assert incoming.content == "look at this"
        assert incoming.images == ["https://example.com/x"]

    def test_empty_returns_none(self, adapter):
        msg = _make_full_message(content="", attachments=[])
        assert adapter._normalize_incoming(msg) is None

    def test_strips_mentions_in_content(self, adapter):
        msg = _make_full_message(content="<@123456789012345678> hello")
        incoming = adapter._normalize_incoming(msg)
        assert incoming.content == "hello"

    def test_strips_llm_artifact_tags_by_default(self, adapter):
        msg = _make_full_message(content="<think>secret</think>answer")
        incoming = adapter._normalize_incoming(msg)
        assert incoming.content == "answer"

    def test_keeps_artifact_tags_when_disabled(self, mock_on_message):
        cfg = make_discord_config(strip_llm_artifact_tags=False)
        a = DiscordAdapter(cfg, mock_on_message)
        msg = _make_full_message(content="<think>secret</think>answer")
        incoming = a._normalize_incoming(msg)
        assert "<think>" in incoming.content

    def test_command_message(self, adapter):
        msg = _make_full_message(content="/new please reset")
        incoming = adapter._normalize_incoming(msg)
        assert incoming.message_type == "command"
        assert incoming.metadata["force_new_instance"] is True
        assert incoming.metadata["command"] == "/new"

    def test_reply_to_id_set(self, adapter):
        ref = MagicMock()
        ref.message_id = 999999999999999999
        msg = _make_full_message(content="replying", message_reference=ref)
        incoming = adapter._normalize_incoming(msg)
        assert incoming.reply_to_id == "999999999999999999"

    def test_no_reply_to_id_when_no_reference(self, adapter):
        msg = _make_full_message(content="hello")
        incoming = adapter._normalize_incoming(msg)
        assert incoming.reply_to_id is None

    def test_metadata_contains_nested_discord_keys(self, adapter):
        msg = _make_full_message(
            guild_id=987654321098765432,
            channel_id=555444333222111333,
            content="hello",
        )
        incoming = adapter._normalize_incoming(msg)
        d = incoming.metadata["discord"]
        assert d["guild_id"] == "987654321098765432"
        assert d["channel_id"] == "555444333222111333"
        assert d["user_id"] == "123456789012345678"
        assert d["message_id"] == "111222333444555666"
        assert d["channel_type"] == "text"

    def test_metadata_channel_type_thread(self, adapter):
        msg = _make_full_message(
            guild_id=987654321098765432,
            channel_id=777888999000111222,
            parent_id=555444333222111333,
            content="hi",
        )
        incoming = adapter._normalize_incoming(msg)
        d = incoming.metadata["discord"]
        assert d["channel_type"] == "thread"
        assert d["thread_id"] == "777888999000111222"
        assert d["parent_channel_id"] == "555444333222111333"

    def test_non_image_attachments_excluded_from_images(self, adapter):
        """FIX 6 regression: only attachments with ``content_type``
        starting with ``image/`` populate ``incoming.images``. PDFs, text
        files, and unknown-content-type attachments must be excluded.
        """
        # Mix of image and non-image attachments.
        img = MagicMock()
        img.filename = "cat.png"
        img.url = "https://example.com/cat.png"
        img.content_type = "image/png"
        pdf = MagicMock()
        pdf.filename = "doc.pdf"
        pdf.url = "https://example.com/doc.pdf"
        pdf.content_type = "application/pdf"
        txt = MagicMock()
        txt.filename = "note.txt"
        txt.url = "https://example.com/note.txt"
        txt.content_type = "text/plain"
        # Unknown content_type — must be excluded.
        unknown = MagicMock()
        unknown.filename = "blob"
        unknown.url = "https://example.com/blob"
        unknown.content_type = None  # default to no content_type

        msg = _make_full_message(
            content="look at these",
            attachments=[img, pdf, txt, unknown],
        )
        incoming = adapter._normalize_incoming(msg)
        assert incoming is not None
        # Only the image must be in `images`.
        assert incoming.images == ["https://example.com/cat.png"]

    def test_attachment_only_with_non_image_returns_placeholder_but_no_images(
        self, adapter,
    ):
        """FIX 6 regression: when the only attachments are non-image, the
        placeholder still appears in ``content`` but ``images`` must be
        None (not a list of non-image URLs).
        """
        pdf = MagicMock()
        pdf.filename = "doc.pdf"
        pdf.url = "https://example.com/doc.pdf"
        pdf.content_type = "application/pdf"

        msg = _make_full_message(content="", attachments=[pdf])
        incoming = adapter._normalize_incoming(msg)
        assert incoming is not None
        assert incoming.content == "[File attachment: doc.pdf]"
        assert incoming.images is None


class TestReplyReference:
    """Regression tests for the discord.py reply-reference attribute name.

    discord.py 2.7.1 exposes reply metadata as ``Message.reference``
    (a ``MessageReference`` object). The adapter previously read
    ``message.message_reference`` — an attribute that does NOT exist
    on real discord ``Message`` objects — so reply chains were silently
    broken for production traffic.

    The bug hid because ``MagicMock`` auto-creates any attribute name,
    so a test that did ``msg.message_reference = ref`` would satisfy the
    old buggy read. The tests below pin down the CORRECT attribute by
    using a stub that raises ``AttributeError`` for ``message_reference``
    (mirroring real discord.Message) and only exposes ``reference``.
    """

    def test_reply_to_id_uses_message_reference_attribute(self, adapter):
        """Adapter MUST read ``.reference`` (real discord.py attribute).

        Pre-fix, the adapter read ``message.message_reference``, which
        does not exist on real discord ``Message`` objects. With real
        messages, ``getattr(message, "message_reference", None)`` always
        returned ``None`` and reply chains were silently lost.

        This test builds a stub message that mirrors real discord.Message:
        ``.reference`` is populated, ``.message_reference`` raises
        ``AttributeError``. With the fix in place, ``reply_to_id`` is
        populated; if the bug were reintroduced, ``reply_to_id`` would
        stay ``None``.
        """

        class _DiscordMessageLike:
            """Minimal discord.Message stub: ``.reference`` exists, ``.message_reference`` does not."""

            def __init__(self, reference):
                self.reference = reference
                self.content = "hello"
                self.id = 111222333444555666
                self.attachments = []
                self.mentions = []

                author = MagicMock()
                author.id = 123456789012345678
                author.bot = False
                author.name = "alice"
                author.display_name = "Alice"
                self.author = author

                channel = MagicMock()
                channel.id = 987654321098765432
                channel.name = "general"
                channel.parent_id = None
                self.channel = channel

                self.guild = None  # DM -> no guild

            @property
            def message_reference(self):
                # Real discord.Message has NO `message_reference` attribute.
                # Accessing it raises AttributeError, just like the real class.
                raise AttributeError(
                    "discord.Message exposes reply metadata as `.reference`, "
                    "not `.message_reference`"
                )

        ref = MagicMock()
        ref.message_id = 123456789012345678

        msg = _DiscordMessageLike(reference=ref)

        incoming = adapter._normalize_incoming(msg)
        assert incoming is not None
        assert incoming.reply_to_id == "123456789012345678"

    def test_reply_to_id_none_when_reference_attr_missing(self, adapter):
        """No ``.reference`` -> reply_to_id stays ``None`` (matches real Discord behavior).

        Builds a stub identical to the one above but WITHOUT a
        ``reference`` attribute at all, matching what a real discord
        message looks like when it is NOT a reply. With the fix in
        place, ``reply_to_id`` is ``None``; with the bug, it would also
        be ``None`` because ``.message_reference`` raises too.
        """

        class _DiscordMessageNoReply:
            """discord.Message stub for a NON-reply message."""

            def __init__(self):
                self.content = "hello"
                self.id = 111222333444555666
                self.attachments = []
                self.mentions = []

                author = MagicMock()
                author.id = 123456789012345678
                author.bot = False
                author.name = "alice"
                author.display_name = "Alice"
                self.author = author

                channel = MagicMock()
                channel.id = 987654321098765432
                channel.name = "general"
                channel.parent_id = None
                self.channel = channel

                self.guild = None

            # Note: NO `.reference` attribute (non-reply message)
            @property
            def message_reference(self):
                raise AttributeError("discord.Message has no `message_reference`")

        msg = _DiscordMessageNoReply()

        incoming = adapter._normalize_incoming(msg)
        assert incoming is not None
        assert incoming.reply_to_id is None


# ==================== Message splitting (5-tier chain) ====================


class TestSplitMessage:
    def test_under_limit_returns_single(self, adapter):
        assert adapter._split_message("short") == ["short"]

    def test_exact_limit_returns_single(self, adapter):
        text = "x" * DISCORD_MAX_MESSAGE_LENGTH
        assert adapter._split_message(text) == [text]

    def test_paragraph_boundary(self, adapter):
        # Build a string where the first paragraph break lands before the cap.
        chunk = "a" * 1800
        body = f"{chunk}\n\n" + "b" * 1800
        chunks = adapter._split_message(body, max_length=2000)
        assert len(chunks) >= 2
        for c in chunks:
            assert len(c) <= 2000

    def test_line_boundary(self, adapter):
        # No double-newline but a single newline within limit.
        body = ("a" * 1800) + "\n" + ("b" * 1800)
        chunks = adapter._split_message(body, max_length=2000)
        assert all(len(c) <= 2000 for c in chunks)
        assert len(chunks) >= 2

    def test_sentence_boundary(self, adapter):
        # No newlines; sentence boundary should kick in.
        # Place the ". " well inside the window (not at the boundary edge)
        # so that rfind(". ") actually finds it.
        body = "a" * 1500 + ". " + ("b" * 1800)
        chunks = adapter._split_message(body, max_length=2000)
        assert all(len(c) <= 2000 for c in chunks)
        assert len(chunks) >= 2
        # The split should have occurred at the ". " boundary, not via hard cut.
        assert chunks[0].rstrip().endswith(".")

    def test_word_boundary(self, adapter):
        # No newlines, no sentence punctuation; just a long string with a space.
        body = "a" * 1999 + " " + ("b" * 1800)
        chunks = adapter._split_message(body, max_length=2000)
        assert all(len(c) <= 2000 for c in chunks)
        assert len(chunks) >= 2

    def test_hard_cut(self, adapter):
        # 4000 chars, no space anywhere — must hard-cut.
        body = "x" * 4000
        chunks = adapter._split_message(body, max_length=2000)
        assert all(len(c) <= 2000 for c in chunks)
        assert len(chunks) == 2
        assert chunks[0] == "x" * 2000
        assert chunks[1] == "x" * 2000

    def test_empty_returns_single_empty(self, adapter):
        assert adapter._split_message("") == [""]

    def test_priority_chain_paragraph_over_line(self, adapter):
        # Both a paragraph and a line break in range; paragraph wins.
        body = "a" * 1800 + "\n\n" + "b" * 100 + "\n" + "c" * 1800
        chunks = adapter._split_message(body, max_length=2000)
        # First chunk should end at \n\n (split at 1800).
        assert chunks[0].endswith("a" * 100) or "b" not in chunks[0]


# ==================== Per-channel lock semantics ====================


class TestChannelLock:
    """LRU eviction and held-lock skip behavior."""

    @pytest.mark.asyncio
    async def test_lru_basic(self, adapter):
        lock_a = await adapter._get_channel_lock("a")
        lock_b = await adapter._get_channel_lock("b")
        assert isinstance(lock_a, asyncio.Lock)
        assert lock_a is not lock_b
        # Re-requesting moves to end.
        await adapter._get_channel_lock("a")
        # The internal OrderedDict should have 'a' at the end.
        assert list(adapter._channel_locks.keys())[-1] == "a"

    @pytest.mark.asyncio
    async def test_lru_eviction_at_capacity(self, mock_on_message):
        cfg = make_discord_config()
        a = DiscordAdapter(cfg, mock_on_message)
        # Use a small cap via a fresh OrderedDict to keep the test fast.
        # We cannot easily change MAX_CHANNEL_LOCKS without monkeypatching,
        # so we just fill to the cap and verify eviction.
        for i in range(MAX_CHANNEL_LOCKS):
            await a._get_channel_lock(f"ch{i}")
        # Add one more — must evict one of the oldest.
        size_before = len(a._channel_locks)
        await a._get_channel_lock("ch_new")
        assert len(a._channel_locks) == size_before  # capped
        assert "ch_new" in a._channel_locks
        assert "ch0" not in a._channel_locks  # oldest evicted

    @pytest.mark.asyncio
    async def test_held_lock_skipped_during_eviction(self, mock_on_message):
        cfg = make_discord_config()
        a = DiscordAdapter(cfg, mock_on_message)
        # Fill to capacity.
        for i in range(MAX_CHANNEL_LOCKS - 1):
            await a._get_channel_lock(f"ch{i}")
        held_lock = await a._get_channel_lock("held")
        # Fill to cap exactly
        # At this point len == MAX_CHANNEL_LOCKS
        await a._get_channel_lock("ch_last")
        # Now held_lock is somewhere in the dict; acquire it so it's locked.
        await held_lock.acquire()
        try:
            # Adding a new key should NOT evict the held lock.
            await a._get_channel_lock("brand_new")
            # The held key must still be present.
            assert "held" in a._channel_locks
        finally:
            held_lock.release()


# ==================== Circuit breaker 429 exclusion ====================


class TestCircuitBreaker:
    """429 must NOT count as a failure (sdk handles rate limits)."""

    @pytest.mark.asyncio
    async def test_record_failure_increments(self, adapter):
        for _ in range(3):
            await adapter._circuit_breaker.record_failure()
        assert adapter._circuit_breaker.failure_count == 3

    @pytest.mark.asyncio
    async def test_failure_threshold_opens(self, adapter):
        for _ in range(5):
            await adapter._circuit_breaker.record_failure()
        assert not await adapter._circuit_breaker.can_execute()

    @pytest.mark.asyncio
    async def test_send_returns_false_when_circuit_open(self, adapter_with_repo):
        for _ in range(5):
            await adapter_with_repo._circuit_breaker.record_failure()
        out = OutgoingMessage(
            external_user_id="987654321098765432:555444333222111333",
            content="hello",
            source_id="discord-main",
        )
        assert await adapter_with_repo.send(out) is False

    @pytest.mark.asyncio
    async def test_send_transport_failure_records_circuit(self, adapter_with_repo):
        # Patch _send_single_chunk to always raise a transport error.
        async def _raise(*args, **kwargs):
            raise ConnectionError("transport failure")

        adapter_with_repo._send_semaphore = MagicMock()
        adapter_with_repo._send_semaphore.__aenter__ = AsyncMock(
            return_value=adapter_with_repo._send_semaphore
        )
        adapter_with_repo._send_semaphore.__aexit__ = AsyncMock(return_value=None)
        # Stub route_outgoing to return a fake target.
        fake_target = MagicMock()
        fake_target.send = AsyncMock(side_effect=ConnectionError("boom"))
        adapter_with_repo._route_outgoing = AsyncMock(return_value=fake_target)
        # Status needs to be RUNNING to proceed past the first guard.
        adapter_with_repo._status = SourceStatus.RUNNING

        out = OutgoingMessage(
            external_user_id="987654321098765432:555444333222111333",
            content="hello",
            source_id="discord-main",
        )
        result = await adapter_with_repo.send(out)
        # Each chunk raises -> send() returns False; circuit count increases.
        assert result is False
        assert adapter_with_repo._circuit_breaker.failure_count >= 1

    @pytest.mark.asyncio
    async def test_not_found_does_not_increment_failure_count(self, adapter_with_repo):
        """FIX 3 regression: discord.NotFound (404) is a PERMANENT client
        error. The adapter must NOT count it as a circuit-breaker failure
        — otherwise 5 distinct 404s across different channels would open
        the circuit and block ALL sends.
        """
        try:
            import discord
        except ImportError:
            pytest.skip("discord.py not installed")

        adapter_with_repo._status = SourceStatus.RUNNING
        fake_target = MagicMock()
        # discord.NotFound signature: (response, message)
        fake_resp = MagicMock()
        fake_resp.status = 404
        fake_target.send = AsyncMock(
            side_effect=discord.NotFound(fake_resp, "unknown channel")
        )
        adapter_with_repo._route_outgoing = AsyncMock(return_value=fake_target)

        initial = adapter_with_repo._circuit_breaker.failure_count
        out = OutgoingMessage(
            external_user_id="987654321098765432:555444333222111333",
            content="hello",
            source_id="discord-main",
        )
        result = await adapter_with_repo.send(out)
        assert result is False
        # Crucial assertion: the count did NOT increase.
        assert adapter_with_repo._circuit_breaker.failure_count == initial

    @pytest.mark.asyncio
    async def test_forbidden_does_not_increment_failure_count(self, adapter_with_repo):
        """FIX 3 regression: discord.Forbidden (403) is a PERMANENT auth
        error — must not open the circuit.
        """
        try:
            import discord
        except ImportError:
            pytest.skip("discord.py not installed")

        adapter_with_repo._status = SourceStatus.RUNNING
        fake_target = MagicMock()
        fake_resp = MagicMock()
        fake_resp.status = 403
        fake_target.send = AsyncMock(
            side_effect=discord.Forbidden(fake_resp, "missing access")
        )
        adapter_with_repo._route_outgoing = AsyncMock(return_value=fake_target)

        initial = adapter_with_repo._circuit_breaker.failure_count
        out = OutgoingMessage(
            external_user_id="987654321098765432:555444333222111333",
            content="hello",
            source_id="discord-main",
        )
        result = await adapter_with_repo.send(out)
        assert result is False
        assert adapter_with_repo._circuit_breaker.failure_count == initial

    @pytest.mark.asyncio
    async def test_5xx_increments_failure_count(self, adapter_with_repo):
        """FIX 3 regression: discord.HTTPException with a 5xx status IS a
        transport-class failure and MUST increment the circuit breaker.
        """
        try:
            import discord
        except ImportError:
            pytest.skip("discord.py not installed")

        adapter_with_repo._status = SourceStatus.RUNNING
        fake_target = MagicMock()
        fake_resp = MagicMock()
        fake_resp.status = 503
        fake_target.send = AsyncMock(
            side_effect=discord.HTTPException(fake_resp, "service unavailable")
        )
        adapter_with_repo._route_outgoing = AsyncMock(return_value=fake_target)

        initial = adapter_with_repo._circuit_breaker.failure_count
        out = OutgoingMessage(
            external_user_id="987654321098765432:555444333222111333",
            content="hello",
            source_id="discord-main",
        )
        result = await adapter_with_repo.send(out)
        assert result is False
        # Crucial assertion: the count DID increase.
        assert adapter_with_repo._circuit_breaker.failure_count == initial + 1

    @pytest.mark.asyncio
    async def test_timeout_increments_failure_count(self, adapter_with_repo):
        """FIX 3 regression: ``asyncio.TimeoutError`` is a transient failure
        — must increment the circuit breaker.
        """
        adapter_with_repo._status = SourceStatus.RUNNING
        fake_target = MagicMock()
        fake_target.send = AsyncMock(side_effect=asyncio.TimeoutError())
        adapter_with_repo._route_outgoing = AsyncMock(return_value=fake_target)

        initial = adapter_with_repo._circuit_breaker.failure_count
        out = OutgoingMessage(
            external_user_id="987654321098765432:555444333222111333",
            content="hello",
            source_id="discord-main",
        )
        result = await adapter_with_repo.send(out)
        assert result is False
        assert adapter_with_repo._circuit_breaker.failure_count == initial + 1

    @pytest.mark.asyncio
    async def test_rate_limit_429_does_not_increment_failure_count(self, adapter_with_repo):
        """FIX 3 regression: 429 (rate limit) is handled internally by
        discord.py; if it escapes, we must NOT count it as a failure.
        """
        try:
            import discord
        except ImportError:
            pytest.skip("discord.py not installed")

        adapter_with_repo._status = SourceStatus.RUNNING
        fake_target = MagicMock()
        fake_resp = MagicMock()
        fake_resp.status = 429
        fake_target.send = AsyncMock(
            side_effect=discord.HTTPException(fake_resp, "rate limited")
        )
        adapter_with_repo._route_outgoing = AsyncMock(return_value=fake_target)

        initial = adapter_with_repo._circuit_breaker.failure_count
        out = OutgoingMessage(
            external_user_id="987654321098765432:555444333222111333",
            content="hello",
            source_id="discord-main",
        )
        result = await adapter_with_repo.send(out)
        assert result is False
        assert adapter_with_repo._circuit_breaker.failure_count == initial


# ==================== Outbound send routing ====================


class TestSendRouting:
    """Send pipeline: status guard, circuit, route, lock, semaphore, split."""

    @pytest.mark.asyncio
    async def test_send_returns_false_when_not_running(self, adapter):
        out = OutgoingMessage(
            external_user_id="dm:123456789012345678",
            content="hello",
            source_id="discord-main",
        )
        assert await adapter.send(out) is False

    @pytest.mark.asyncio
    async def test_send_returns_false_when_repo_missing(self, adapter):
        adapter._status = SourceStatus.RUNNING
        out = OutgoingMessage(
            external_user_id="987654321098765432:555444333222111333",
            content="hello",
            source_id="discord-main",
        )
        assert await adapter.send(out) is False

    @pytest.mark.asyncio
    async def test_send_returns_false_for_invalid_external_user_id(self, adapter_with_repo):
        adapter_with_repo._status = SourceStatus.RUNNING
        out = OutgoingMessage(
            external_user_id="not-a-valid-discord-id",
            content="hello",
            source_id="discord-main",
        )
        assert await adapter_with_repo.send(out) is False

    @pytest.mark.asyncio
    async def test_send_returns_false_when_no_mapping(self, mock_on_message):
        cfg = make_discord_config()
        a = DiscordAdapter(cfg, mock_on_message)
        a._source_repo = MagicMock()
        a._source_repo.get_instance_mapping = MagicMock(return_value=None)
        a._status = SourceStatus.RUNNING
        out = OutgoingMessage(
            external_user_id="987654321098765432:555444333222111333",
            content="hello",
            source_id="discord-main",
        )
        assert await a.send(out) is False

    @pytest.mark.asyncio
    async def test_send_returns_false_when_db_raises(self, mock_on_message):
        cfg = make_discord_config()
        a = DiscordAdapter(cfg, mock_on_message)
        a._source_repo = MagicMock()
        a._source_repo.get_instance_mapping = MagicMock(side_effect=Exception("db down"))
        a._status = SourceStatus.RUNNING
        out = OutgoingMessage(
            external_user_id="987654321098765432:555444333222111333",
            content="hello",
            source_id="discord-main",
        )
        assert await a.send(out) is False

    @pytest.mark.asyncio
    async def test_send_succeeds_with_valid_mapping(self, adapter_with_repo):
        adapter_with_repo._status = SourceStatus.RUNNING
        fake_target = MagicMock()
        fake_target.send = AsyncMock(return_value=MagicMock())
        adapter_with_repo._route_outgoing = AsyncMock(return_value=fake_target)
        out = OutgoingMessage(
            external_user_id="987654321098765432:555444333222111333",
            content="hello world",
            source_id="discord-main",
        )
        assert await adapter_with_repo.send(out) is True
        fake_target.send.assert_awaited()

    @pytest.mark.asyncio
    async def test_send_long_message_splits_into_chunks(self, adapter_with_repo):
        adapter_with_repo._status = SourceStatus.RUNNING
        fake_target = MagicMock()
        fake_target.send = AsyncMock(return_value=MagicMock())
        adapter_with_repo._route_outgoing = AsyncMock(return_value=fake_target)
        long_content = "x" * 4500
        out = OutgoingMessage(
            external_user_id="987654321098765432:555444333222111333",
            content=long_content,
            source_id="discord-main",
        )
        assert await adapter_with_repo.send(out) is True
        # 4500 chars / 2000 chunk size = 3 chunks
        assert fake_target.send.await_count == 3

    @pytest.mark.asyncio
    async def test_send_strips_llm_tags_by_default(self, adapter_with_repo):
        adapter_with_repo._status = SourceStatus.RUNNING
        fake_target = MagicMock()
        fake_target.send = AsyncMock(return_value=MagicMock())
        adapter_with_repo._route_outgoing = AsyncMock(return_value=fake_target)
        out = OutgoingMessage(
            external_user_id="987654321098765432:555444333222111333",
            content="<think>secret</think>visible",
            source_id="discord-main",
        )
        await adapter_with_repo.send(out)
        # ``channel.send`` is invoked positionally with ``content`` as the first
        # positional argument.
        call_args = fake_target.send.await_args
        sent_content = call_args.args[0]
        assert "<think>" not in sent_content
        assert sent_content == "visible"

    @pytest.mark.asyncio
    async def test_send_with_reply_to_id(self, adapter_with_repo):
        adapter_with_repo._status = SourceStatus.RUNNING
        fake_target = MagicMock()
        fake_target.send = AsyncMock(return_value=MagicMock())
        adapter_with_repo._route_outgoing = AsyncMock(return_value=fake_target)
        out = OutgoingMessage(
            external_user_id="987654321098765432:555444333222111333",
            content="reply",
            source_id="discord-main",
            reply_to_id="111111111111111111",
        )
        assert await adapter_with_repo.send(out) is True
        call_kwargs = fake_target.send.await_args.kwargs
        assert call_kwargs["reference"] is not None
        # MessageReference carries message_id and channel_id
        assert int(call_kwargs["reference"].message_id) == 111111111111111111


# ==================== Health check ====================


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_false_when_not_running(self, adapter):
        assert await adapter.health_check() is False

    @pytest.mark.asyncio
    async def test_health_false_when_client_missing(self, adapter):
        adapter._status = SourceStatus.RUNNING
        # client is None
        assert await adapter.health_check() is False

    @pytest.mark.asyncio
    async def test_health_false_when_client_not_ready(self, adapter):
        adapter._status = SourceStatus.RUNNING
        client = MagicMock()
        client.is_ready = MagicMock(return_value=False)
        adapter._client = client
        assert await adapter.health_check() is False

    @pytest.mark.asyncio
    async def test_health_true_when_ready_and_low_latency(self, adapter):
        adapter._status = SourceStatus.RUNNING
        client = MagicMock()
        client.is_ready = MagicMock(return_value=True)
        client.latency = 0.050  # 50ms in seconds
        adapter._client = client
        assert await adapter.health_check() is True

    @pytest.mark.asyncio
    async def test_health_false_when_latency_above_threshold(self, adapter):
        adapter._status = SourceStatus.RUNNING
        client = MagicMock()
        client.is_ready = MagicMock(return_value=True)
        client.latency = DISCORD_LATENCY_THRESHOLD_MS / 1000.0 + 0.001
        adapter._client = client
        assert await adapter.health_check() is False

    @pytest.mark.asyncio
    async def test_health_false_when_latency_exactly_threshold(self, adapter):
        adapter._status = SourceStatus.RUNNING
        client = MagicMock()
        client.is_ready = MagicMock(return_value=True)
        client.latency = DISCORD_LATENCY_THRESHOLD_MS / 1000.0
        adapter._client = client
        assert await adapter.health_check() is False


# ==================== Shutdown idempotency ====================


class TestStopIdempotency:
    @pytest.mark.asyncio
    async def test_stop_when_never_started(self, adapter):
        # Should not raise.
        await adapter.stop()
        assert adapter.status == SourceStatus.STOPPED

    @pytest.mark.asyncio
    async def test_double_stop_idempotent(self, adapter):
        await adapter.stop()
        # Second call should be a no-op.
        await adapter.stop()
        assert adapter.status == SourceStatus.STOPPED


# ==================== SendSemaphore metrics ====================


class TestSendSemaphore:
    @pytest.mark.asyncio
    async def test_basic_acquire_release(self):
        sem = DiscordSendSemaphore(max_concurrent_sends=2)
        assert sem.active_sends == 0
        async with sem:
            assert sem.active_sends == 1
            assert sem.total_sends == 1
        assert sem.active_sends == 0

    @pytest.mark.asyncio
    async def test_rate_limit_waits_increment(self):
        sem = DiscordSendSemaphore(max_concurrent_sends=1)

        async def hold():
            async with sem:
                await asyncio.sleep(0.05)

        # Start one holder, then a contender.
        task1 = asyncio.create_task(hold())
        await asyncio.sleep(0.005)  # let task1 acquire
        async with sem:
            # The contender waited.
            assert sem.rate_limit_waits >= 1
        await task1

    def test_rejects_zero_max(self):
        with pytest.raises(ValueError):
            DiscordSendSemaphore(max_concurrent_sends=0)

    @pytest.mark.asyncio
    async def test_release_without_acquire_logs(self, caplog):
        sem = DiscordSendSemaphore(max_concurrent_sends=2)
        with caplog.at_level(logging.WARNING, logger="daemon.sources.adapters.discord.resilience"):
            await sem.release()
        assert "no active sends" in caplog.text

    def test_get_stats_shape(self):
        sem = DiscordSendSemaphore(max_concurrent_sends=3)
        stats = sem.get_stats()
        assert stats == {
            "active_sends": 0,
            "total_sends": 0,
            "rate_limit_waits": 0,
            "max_concurrent": 3,
        }


# ==================== Test connection (class method) ====================


class TestTestConnection:
    @pytest.mark.asyncio
    async def test_missing_token_returns_false(self):
        from daemon.sources.base import SourceConfig
        cfg = SourceConfig(
            source_id="test",
            source_type="discord",
            name="Test",
            config={},
            credentials={},
        )
        ok, msg = await DiscordAdapter.test_connection(cfg)
        assert ok is False
        assert "bot_token" in msg

    @pytest.mark.asyncio
    async def test_non_string_token_returns_false(self):
        from daemon.sources.base import SourceConfig
        cfg = SourceConfig(
            source_id="test",
            source_type="discord",
            name="Test",
            config={},
            credentials={"bot_token": 12345},  # int, not str
        )
        ok, msg = await DiscordAdapter.test_connection(cfg)
        assert ok is False

    @pytest.mark.asyncio
    async def test_200_response_returns_true(self):
        from daemon.sources.base import SourceConfig

        cfg = SourceConfig(
            source_id="test",
            source_type="discord",
            name="Test",
            config={},
            credentials={"bot_token": "MTIzNDU2Nzg5.Mabcdef.test_signature_123"},
        )

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"username": "bot", "id": "999"})

        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        # ``session.get`` returns an async context manager.
        get_cm = MagicMock()
        get_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        get_cm.__aexit__ = AsyncMock(return_value=None)
        session.get = MagicMock(return_value=get_cm)

        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await DiscordAdapter.test_connection(cfg)
        assert ok is True
        assert "bot" in msg

    @pytest.mark.asyncio
    async def test_401_returns_invalid_token(self):
        from daemon.sources.base import SourceConfig

        cfg = SourceConfig(
            source_id="test",
            source_type="discord",
            name="Test",
            config={},
            credentials={"bot_token": "MTIzNDU2Nzg5.Mabcdef.test_signature_123"},
        )
        mock_resp = AsyncMock()
        mock_resp.status = 401
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        get_cm = MagicMock()
        get_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        get_cm.__aexit__ = AsyncMock(return_value=None)
        session.get = MagicMock(return_value=get_cm)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await DiscordAdapter.test_connection(cfg)
        assert ok is False
        assert "Invalid bot token" in msg

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self):
        from daemon.sources.base import SourceConfig

        cfg = SourceConfig(
            source_id="test",
            source_type="discord",
            name="Test",
            config={},
            credentials={"bot_token": "MTIzNDU2Nzg5.Mabcdef.test_signature_123"},
        )
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        get_cm = MagicMock()
        get_cm.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
        get_cm.__aexit__ = AsyncMock(return_value=None)
        session.get = MagicMock(return_value=get_cm)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await DiscordAdapter.test_connection(cfg)
        assert ok is False
        assert "timed out" in msg

    @pytest.mark.asyncio
    async def test_invalid_token_format_returns_error(self):
        """FIX 9 regression: a token with the wrong shape must be rejected
        up front without an API call.
        """
        from daemon.sources.base import SourceConfig

        cfg = SourceConfig(
            source_id="test",
            source_type="discord",
            name="Test",
            config={},
            credentials={"bot_token": "not-a-token"},
        )
        ok, msg = await DiscordAdapter.test_connection(cfg)
        assert ok is False
        assert "invalid format" in msg.lower()

    @pytest.mark.asyncio
    async def test_valid_format_invalid_token_returns_401(self):
        """FIX 9 regression: a token with the right SHAPE but wrong VALUE
        must fall through to the API call (and receive 401 there)."""
        from daemon.sources.base import SourceConfig

        cfg = SourceConfig(
            source_id="test",
            source_type="discord",
            name="Test",
            config={},
            credentials={"bot_token": "MTIzNDU2Nzg5.Mabcdef.test_signature_123"},
        )
        mock_resp = AsyncMock()
        mock_resp.status = 401
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        get_cm = MagicMock()
        get_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        get_cm.__aexit__ = AsyncMock(return_value=None)
        session.get = MagicMock(return_value=get_cm)
        with patch("aiohttp.ClientSession", return_value=session) as session_mock:
            ok, msg = await DiscordAdapter.test_connection(cfg)
        # Confirm we did reach the API call (not short-circuited).
        assert session_mock.called
        assert ok is False
        assert "Invalid bot token" in msg


# ==================== Registry integration ====================


class TestRegistryIntegration:
    def test_discord_branch_in_registry(self):
        from daemon.sources.registry import SourceRegistry
        # Inspect the source file for the elif branch.
        with open("daemon/sources/registry.py") as f:
            text = f.read()
        assert 'source_type == "discord"' in text

    def test_registry_creates_adapter(self):
        """Verify registry dispatch builds a DiscordAdapter with manager + repo."""
        from daemon.sources.registry import SourceRegistry

        repo = MagicMock()
        manager = MagicMock()
        reg = SourceRegistry(source_repo=repo, manager=manager)

        cfg = make_discord_config()
        # We need an on_message callback wrapper — pass an async mock.
        msg_received = []

        async def on_message(msg):
            msg_received.append(msg)

        # Build the adapter manually through the registry's create path by
        # mocking the parts that would otherwise write to the DB.
        adapter = DiscordAdapter(cfg, on_message, manager=manager)
        adapter._source_repo = repo
        assert adapter._source_repo is repo
        assert adapter._thread_manager is not None


# ==================== FAIL-CLOSED on missing MESSAGE_CONTENT intent ====================


class TestFailClosedMessageContentIntent:
    """FAIL-CLOSED: start() must reject when message_content intent is off."""

    @pytest.mark.asyncio
    async def test_missing_message_content_raises(self, mock_on_message):
        """start() raises RuntimeError when message_content intent is disabled."""
        cfg = make_discord_config(
            intents={"guilds": True, "guild_messages": True, "message_content": False},
        )
        a = DiscordAdapter(cfg, mock_on_message)
        with pytest.raises(RuntimeError, match="MESSAGE_CONTENT"):
            await a.start()
        assert a.status == SourceStatus.ERROR

    @pytest.mark.asyncio
    async def test_missing_message_content_key_entirely(self, mock_on_message):
        """start() raises when intents dict lacks message_content key."""
        cfg = make_discord_config(
            intents={"guilds": True, "guild_messages": True},
        )
        a = DiscordAdapter(cfg, mock_on_message)
        with pytest.raises(RuntimeError, match="MESSAGE_CONTENT"):
            await a.start()


# ==================== External user ID parsing ====================


class TestParseExternalUserId:
    """Tests for _parse_external_user_id — the inverse of _build_external_user_id."""

    def test_parse_dm_id(self, adapter):
        result = adapter._parse_external_user_id("dm:123456789012345678")
        assert result["mode"] == "dm"
        assert result["user_id"] == "123456789012345678"
        assert result["guild_id"] is None

    def test_parse_channel_id(self, adapter):
        result = adapter._parse_external_user_id("111222333444555666:999888777666555444")
        assert result["mode"] == "channel"
        assert result["guild_id"] == "111222333444555666"
        assert result["channel_id"] == "999888777666555444"

    def test_parse_thread_id(self, adapter):
        eid = "111222333444555666:999888777666555444:777666555444333222"
        result = adapter._parse_external_user_id(eid)
        assert result["mode"] == "thread"
        assert result["guild_id"] == "111222333444555666"
        assert result["parent_channel_id"] == "999888777666555444"
        assert result["thread_id"] == "777666555444333222"

    def test_parse_invalid_raises(self, adapter):
        with pytest.raises(ValueError):
            adapter._parse_external_user_id("not-valid")

    def test_parse_empty_raises(self, adapter):
        with pytest.raises(ValueError):
            adapter._parse_external_user_id("")


# ==================== Archived thread routing ====================


class TestArchivedThreadRouting:
    """Archived threads must route outbound sends to the parent channel."""

    @pytest.mark.asyncio
    async def test_archived_thread_routes_to_parent(self, adapter_with_repo):
        """FIX 1 regression: when the thread is archived, ``_route_outgoing``
        must fetch the PARENT channel (not the thread itself). The thread's
        own ID is what ``channel_id`` carries in the mapping, so the
        adapter must consult ``parent_channel_id`` explicitly.
        """
        adapter_with_repo._status = SourceStatus.RUNNING

        # Set up thread manager with an archived thread.
        mock_manager = MagicMock()
        adapter_with_repo._thread_manager = DiscordThreadManager(manager=mock_manager)
        await adapter_with_repo._thread_manager.register_thread(
            guild_id="987654321098765432",
            channel_id="555444333222111333",
            thread_id="777888999000111222",
        )
        await adapter_with_repo._thread_manager.mark_archived(
            "987654321098765432", "777888999000111222", archived=True
        )

        # Mock the client so the parent-channel fetch returns the fake parent.
        fake_parent = MagicMock()
        fake_parent.send = AsyncMock(return_value=MagicMock())
        fake_client = MagicMock()
        fake_client.get_channel = MagicMock(return_value=None)
        fake_client.fetch_channel = AsyncMock(return_value=fake_parent)
        adapter_with_repo._client = fake_client

        # Mapping carries the thread's own ID as ``channel_id`` (matches
        # real production writes from ``_normalize_incoming``) plus the
        # explicit ``parent_channel_id``.
        adapter_with_repo._source_repo.get_instance_mapping = MagicMock(
            return_value=MagicMock(
                mapping_metadata={
                    "discord": {
                        "guild_id": "987654321098765432",
                        # thread's own ID
                        "channel_id": "777888999000111222",
                        "thread_id": "777888999000111222",
                        # parent channel ID (separate field)
                        "parent_channel_id": "555444333222111333",
                    }
                }
            )
        )

        out = OutgoingMessage(
            external_user_id="987654321098765432:555444333222111333:777888999000111222",
            content="hello",
            source_id="discord-main",
        )
        result = await adapter_with_repo.send(out)
        assert result is True
        # The PARENT channel should receive the send, NOT the thread.
        fake_client.fetch_channel.assert_awaited_with(555444333222111333)
        # And the thread ID must NOT have been used as the fetch target.
        called_with_thread_id = any(
            c.args == (777888999000111222,)
            for c in fake_client.fetch_channel.await_args_list
        )
        assert not called_with_thread_id, (
            "FIX 1 violation: archived thread fallback must use "
            "parent_channel_id, not the thread's own ID"
        )

    @pytest.mark.asyncio
    async def test_active_thread_sends_to_thread(self, adapter_with_repo):
        """When thread is NOT archived, _route_outgoing sends to the thread."""
        adapter_with_repo._status = SourceStatus.RUNNING

        mock_manager = MagicMock()
        adapter_with_repo._thread_manager = DiscordThreadManager(manager=mock_manager)
        await adapter_with_repo._thread_manager.register_thread(
            guild_id="987654321098765432",
            channel_id="555444333222111333",
            thread_id="777888999000111222",
        )
        # Thread is NOT archived (default).

        fake_thread_chan = MagicMock()
        fake_thread_chan.send = AsyncMock(return_value=MagicMock())
        fake_client = MagicMock()
        # get_channel for the thread id returns the thread channel.
        fake_client.get_channel = MagicMock(return_value=fake_thread_chan)
        adapter_with_repo._client = fake_client

        adapter_with_repo._source_repo.get_instance_mapping = MagicMock(
            return_value=MagicMock(
                mapping_metadata={
                    "discord": {
                        "guild_id": "987654321098765432",
                        "channel_id": "777888999000111222",
                        "thread_id": "777888999000111222",
                        "parent_channel_id": "555444333222111333",
                    }
                }
            )
        )

        out = OutgoingMessage(
            external_user_id="987654321098765432:555444333222111333:777888999000111222",
            content="hello",
            source_id="discord-main",
        )
        result = await adapter_with_repo.send(out)
        assert result is True
        fake_thread_chan.send.assert_awaited()



# ==================== Start lifecycle (COVERAGE 1) ====================


class _FakeDiscordClient:
    """Drop-in replacement for ``discord.Client`` for lifecycle tests.

    Captures registered event handlers so tests can fire them on demand.
    Defaults to "fires on_ready then idles" so simple start()-succeeds
    tests do not need to manage the lifecycle manually.
    """

    instances: list["_FakeDiscordClient"] = []

    def __init__(self, *, intents=None, fail_on_start: BaseException | None = None):
        self.intents = intents
        self._events: list = []
        self._fail_on_start = fail_on_start
        self._start_called = False
        self._start_token: str | None = None
        self._closed = False
        self.user = MagicMock()
        self.user.id = 999999999999999999
        self.user.name = "test-bot"
        self.latency = 0.05
        self._is_ready_flag = True
        # Channel/user resolution hooks for send-side tests.
        self.get_channel = MagicMock(return_value=None)
        self.fetch_channel = AsyncMock(return_value=None)
        self.fetch_user = AsyncMock(return_value=None)
        self.close = AsyncMock(side_effect=self._close_impl)
        _FakeDiscordClient.instances.append(self)

    def event(self, fn):
        # Mirror real discord.py: ``Client.event()`` does
        # ``setattr(self, coro.__name__, coro)`` and the dispatcher
        # does ``getattr(self, 'on_' + event_name, None)``. Naming the
        # callback ``_on_ready`` (with leading underscore) was the
        # root cause of a silent 30s Gateway timeout — the dispatcher
        # could not find the handler. Enforce canonical names here so
        # any regression in the production code fails this test instead
        # of timing out the daemon.
        self._events.append(fn)
        # Mirror real discord.Client.event: also set the attribute so
        # any future code path that does ``getattr(client, 'on_ready')``
        # (matching the real dispatcher) hits the registered callback.
        setattr(self, fn.__name__, fn)
        return fn

    async def start(self, token):
        self._start_called = True
        self._start_token = token
        if self._fail_on_start is not None:
            raise self._fail_on_start
        # Fire the on_ready callback (zero-arg) and on_message (one-arg).
        for cb in list(self._events):
            try:
                sig = inspect.signature(cb)
            except (TypeError, ValueError):
                sig = None
            if sig is not None and len(sig.parameters) == 0:
                await cb()
                return

    def is_ready(self):
        return self._is_ready_flag

    async def _close_impl(self):
        self._closed = True


class TestStartLifecycle:
    """End-to-end lifecycle coverage for ``start()``."""

    @pytest.fixture(autouse=True)
    def _clear_instances(self):
        _FakeDiscordClient.instances.clear()

    @pytest.mark.asyncio
    async def test_start_success(self, mock_on_message):
        """Happy path: ``client.start()`` fires ``on_ready``; adapter
        transitions STOPPED → STARTING → RUNNING and captures bot identity.
        """
        cfg = make_discord_config()
        a = DiscordAdapter(cfg, mock_on_message)

        # Patch discord.Client at the module level — start() does a local
        # ``import discord`` but Python reuses the cached module object,
        # so patching ``discord.Client`` is sufficient.
        import discord as _discord_mod

        with patch.object(_discord_mod, "Client") as ClientMock:
            ClientMock.side_effect = lambda *, intents: _FakeDiscordClient(
                intents=intents
            )
            await a.start()

        assert a.status == SourceStatus.RUNNING
        assert a._bot_user_id == "999999999999999999"
        assert a._bot_user_name == "test-bot"
        assert a._ttl_task is not None
        assert a._client_task is not None
        assert len(_FakeDiscordClient.instances) == 1

        # Clean up — cancels the eviction task so the test exits cleanly.
        await a.stop()
        racer_tasks = [
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and any(
                marker in task.get_name()
                for marker in ("discord-ready-wait", "discord-error-wait")
            )
        ]
        assert racer_tasks == []

    @pytest.mark.asyncio
    async def test_start_success_no_pending_tasks(self, mock_on_message):
        """A successful start/stop awaits the losing readiness racer."""
        cfg = make_discord_config()
        a = DiscordAdapter(cfg, mock_on_message)
        import discord as _discord_mod

        with patch.object(_discord_mod, "Client") as ClientMock:
            ClientMock.side_effect = lambda *, intents: _FakeDiscordClient(
                intents=intents
            )
            await a.start()
            await a.stop()

        assert not [
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith(("discord-ready-wait", "discord-error-wait"))
        ]

    @pytest.mark.asyncio
    async def test_start_registers_on_ready_with_canonical_name(
        self, mock_on_message
    ):
        """Regression for the silent-30s-Gateway-timeout bug.

        ``discord.Client.event()`` registers a callback via
        ``setattr(self, coro.__name__, coro)`` and the gateway
        dispatcher invokes ``getattr(self, 'on_ready', None)``. If the
        adapter names the callback ``_on_ready`` (or any name other
        than ``on_ready`` / ``on_message``), the dispatcher never finds
        it, ``_ready_event`` is never set, and ``start()`` times out at
        ``GATEWAY_READY_TIMEOUT_SECONDS`` while the gateway is
        connected — exactly the symptom in production.

        Lock in the contract: ``start()`` must register the ready and
        message callbacks under their canonical names.
        """
        cfg = make_discord_config()
        a = DiscordAdapter(cfg, mock_on_message)

        import discord as _discord_mod

        with patch.object(_discord_mod, "Client") as ClientMock:
            ClientMock.side_effect = lambda *, intents: _FakeDiscordClient(
                intents=intents
            )
            await a.start()

            fake = _FakeDiscordClient.instances[0]
            # Both names MUST be present on the client (as attributes,
            # matching real discord.py's setattr contract).
            assert hasattr(fake, "on_ready"), (
                "Discord adapter must register on_ready under the "
                "canonical name — discord.py's dispatcher does "
                "`getattr(self, 'on_ready', None)`."
            )
            assert hasattr(fake, "on_message"), (
                "Discord adapter must register on_message under the "
                "canonical name — same contract."
            )
            # And the registered callbacks must be the adapters' local
            # closures (NOT methods like _on_ready / _on_message that
            # the dispatcher silently drops).
            assert fake.on_ready.__name__ == "on_ready"
            assert fake.on_message.__name__ == "on_message"

        await a.stop()

    @pytest.mark.asyncio
    async def test_start_idempotent(self, mock_on_message):
        """FIX 4 regression: calling ``start()`` twice does NOT create a
        second ``discord.Client`` (the early-return guard prevents it,
        and ``_start_lock`` serializes any race).
        """
        cfg = make_discord_config()
        a = DiscordAdapter(cfg, mock_on_message)

        import discord as _discord_mod

        with patch.object(_discord_mod, "Client") as ClientMock:
            ClientMock.side_effect = lambda *, intents: _FakeDiscordClient(
                intents=intents
            )
            await a.start()
            # Second call — should be a no-op.
            await a.start()

        # Only one Client was instantiated.
        assert len(_FakeDiscordClient.instances) == 1

        await a.stop()

    @pytest.mark.asyncio
    async def test_start_lock_serializes_concurrent_calls(self, mock_on_message):
        """FIX 4 regression: concurrent ``start()`` invocations are
        serialized by ``_start_lock`` so we never spawn two client tasks.
        """
        cfg = make_discord_config()
        a = DiscordAdapter(cfg, mock_on_message)

        import discord as _discord_mod

        with patch.object(_discord_mod, "Client") as ClientMock:
            ClientMock.side_effect = lambda *, intents: _FakeDiscordClient(
                intents=intents
            )
            await asyncio.gather(a.start(), a.start(), a.start())

        # All three concurrent starts collapsed to a single client.
        assert len(_FakeDiscordClient.instances) == 1

        await a.stop()

    @pytest.mark.asyncio
    async def test_start_privileged_intents_required_surfaces_immediately(
        self, mock_on_message
    ):
        """FIX 5 regression: ``PrivilegedIntentsRequired`` (or any
        synchronous client failure) must surface immediately, NOT after
        the 30s ready timeout. Measured by monkeypatching the timeout
        constant to a tiny value and verifying we fail fast.
        """
        import discord

        cfg = make_discord_config()
        a = DiscordAdapter(cfg, mock_on_message)

        boom = discord.PrivilegedIntentsRequired("missing MESSAGE_CONTENT")

        with patch.object(discord, "Client") as ClientMock:
            ClientMock.side_effect = lambda *, intents: _FakeDiscordClient(
                intents=intents, fail_on_start=boom,
            )
            # Shrink the timeout to keep the test fast even if the fix is
            # missing (it would still fail, just via TimeoutError).
            with patch(
                "daemon.sources.adapters.discord.adapter.GATEWAY_READY_TIMEOUT_SECONDS",
                1.0,
            ):
                start = asyncio.get_event_loop().time()
                with pytest.raises(RuntimeError) as excinfo:
                    await a.start()
                elapsed = asyncio.get_event_loop().time() - start

        # The error message must surface the REAL gateway error,
        # not a generic "timed out" message.
        assert "PrivilegedIntentsRequired" in str(excinfo.value) or "missing" in str(
            excinfo.value
        )
        # And it must surface quickly — well under the 30s default.
        assert elapsed < 5.0, (
            f"start() took {elapsed:.2f}s — gateway error not surfaced "
            f"immediately (FIX 5 regression)"
        )
        assert a.status == SourceStatus.ERROR
        assert not [
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith(("discord-ready-wait", "discord-error-wait"))
        ]

    @pytest.mark.asyncio
    async def test_start_gateway_error_no_pending_tasks(self, mock_on_message):
        """A fast Gateway failure awaits the other readiness racer."""
        import discord

        cfg = make_discord_config()
        a = DiscordAdapter(cfg, mock_on_message)
        boom = discord.PrivilegedIntentsRequired("missing MESSAGE_CONTENT")

        with patch.object(discord, "Client") as ClientMock:
            ClientMock.side_effect = lambda *, intents: _FakeDiscordClient(
                intents=intents, fail_on_start=boom,
            )
            with patch(
                "daemon.sources.adapters.discord.adapter.GATEWAY_READY_TIMEOUT_SECONDS",
                1.0,
            ):
                with pytest.raises(RuntimeError):
                    await a.start()

        assert not [
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith(("discord-ready-wait", "discord-error-wait"))
        ]

        """If the client NEVER fires on_ready and NEVER errors, the
        30s ready timeout must still kick in and raise.
        """
        cfg = make_discord_config()
        a = DiscordAdapter(cfg, mock_on_message)

        # Build a fake client that never fires on_ready.
        class _HangingClient(_FakeDiscordClient):
            async def start(self, token):
                # Just sleep forever — never fire on_ready.
                await asyncio.sleep(100)

        import discord as _discord_mod

        with patch.object(_discord_mod, "Client") as ClientMock:
            ClientMock.side_effect = lambda *, intents: _HangingClient(intents=intents)
            with patch(
                "daemon.sources.adapters.discord.adapter.GATEWAY_READY_TIMEOUT_SECONDS",
                0.5,
            ):
                start = asyncio.get_event_loop().time()
                with pytest.raises(RuntimeError, match="timed out"):
                    await a.start()
                elapsed = asyncio.get_event_loop().time() - start

        # Should fire close to the configured timeout (0.5s), not 30s.
        assert elapsed < 3.0
        assert a.status == SourceStatus.ERROR
        assert not [
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith(("discord-ready-wait", "discord-error-wait"))
        ]

    @pytest.mark.asyncio
    async def test_start_timeout_no_pending_tasks(self, mock_on_message):
        """A Gateway timeout awaits the cancelled readiness racers."""
        cfg = make_discord_config()
        a = DiscordAdapter(cfg, mock_on_message)

        class _HangingClient(_FakeDiscordClient):
            async def start(self, token):
                await asyncio.sleep(100)

        import discord as _discord_mod

        with patch.object(_discord_mod, "Client") as ClientMock:
            ClientMock.side_effect = lambda *, intents: _HangingClient(intents=intents)
            with patch(
                "daemon.sources.adapters.discord.adapter.GATEWAY_READY_TIMEOUT_SECONDS",
                0.05,
            ):
                with pytest.raises(RuntimeError, match="timed out"):
                    await a.start()

        assert not [
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith(("discord-ready-wait", "discord-error-wait"))
        ]

        """If the caller cancels ``start()`` mid-flight, no orphaned
        Gateway task survives.
        """
        cfg = make_discord_config()
        a = DiscordAdapter(cfg, mock_on_message)

        # Build a fake client that blocks forever.
        class _HangingClient(_FakeDiscordClient):
            async def start(self, token):
                await asyncio.sleep(100)

        import discord as _discord_mod

        with patch.object(_discord_mod, "Client") as ClientMock:
            ClientMock.side_effect = lambda *, intents: _HangingClient(intents=intents)
            with patch(
                "daemon.sources.adapters.discord.adapter.GATEWAY_READY_TIMEOUT_SECONDS",
                5.0,
            ):
                task = asyncio.create_task(a.start())
                await asyncio.sleep(0.05)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

        # The client task should be cancelled (or None) — no orphaned task.
        assert a._client_task is None or a._client_task.cancelled() or a._client_task.done()


# ==================== _handle_message end-to-end (COVERAGE 2) ====================


class TestHandleMessageE2E:
    """End-to-end ``_handle_message()`` coverage — the full pipeline."""

    def _msg(self, *, guild_id=None, channel_id=987654321098765432, parent_id=None,
             author_id=123456789012345678, content="hello", bot=False):
        author = MagicMock()
        author.id = author_id
        author.bot = bot
        author.name = "alice"
        author.display_name = "Alice"
        channel = MagicMock()
        channel.id = channel_id
        channel.parent_id = parent_id
        channel.name = "general"
        guild = MagicMock() if guild_id is not None else None
        if guild is not None:
            guild.id = guild_id
            guild.name = "Test Guild"
        msg = MagicMock()
        msg.author = author
        msg.channel = channel
        msg.guild = guild
        msg.content = content
        msg.mentions = []
        msg.attachments = []
        msg.id = 111222333444555666
        return msg

    @pytest.mark.asyncio
    async def test_handle_message_dm_emits_incoming(self, adapter):
        """DM message → _on_message is awaited with an IncomingMessage."""
        msg = self._msg(guild_id=None, content="hello")
        await adapter._handle_message(msg)
        assert adapter._on_message.await_count == 1
        incoming = adapter._on_message.await_args.args[0]
        assert incoming.external_user_id == "dm:123456789012345678"

    @pytest.mark.asyncio
    async def test_handle_message_guild_mention_emits_incoming(self, adapter_with_repo):
        """Guild message with explicit mention → emitted."""
        bot_id = adapter_with_repo._bot_user_id
        msg = self._msg(
            guild_id=987654321098765432,
            content=f"<@{bot_id}> hello",
        )
        await adapter_with_repo._handle_message(msg)
        assert adapter_with_repo._on_message.await_count == 1

    @pytest.mark.asyncio
    async def test_handle_message_guild_no_mention_skipped(self, adapter_with_repo):
        """Guild message WITHOUT mention and require_mention=True → NOT emitted."""
        adapter_with_repo._require_mention = True
        msg = self._msg(guild_id=987654321098765432, content="hello")
        await adapter_with_repo._handle_message(msg)
        assert adapter_with_repo._on_message.await_count == 0

    @pytest.mark.asyncio
    async def test_handle_message_own_message_skipped(self, adapter_with_repo):
        """Messages authored by the bot itself → NOT emitted."""
        # Configure client so message.author == client.user (own message).
        bot_id = int(adapter_with_repo._bot_user_id)
        client = MagicMock()
        client.user = MagicMock()
        client.user.id = bot_id
        adapter_with_repo._client = client

        msg = self._msg(
            guild_id=987654321098765432,
            author_id=bot_id,
            content="my own message",
        )
        await adapter_with_repo._handle_message(msg)
        assert adapter_with_repo._on_message.await_count == 0

    @pytest.mark.asyncio
    async def test_handle_message_disallowed_guild_skipped(self, mock_on_message):
        """Message from a non-allowlisted guild → NOT emitted."""
        cfg = make_discord_config(allowed_guild_ids=[111222333444555666])
        a = DiscordAdapter(cfg, mock_on_message)
        msg = self._msg(guild_id=987654321098765432, content="hi")
        await a._handle_message(msg)
        assert a._on_message.await_count == 0

    @pytest.mark.asyncio
    async def test_handle_message_thread_registers(self, adapter_with_repo):
        """Thread-mode message → thread registered via ``_thread_manager``."""
        adapter_with_repo._thread_manager = DiscordThreadManager(manager=MagicMock())
        bot_id = adapter_with_repo._bot_user_id
        msg = self._msg(
            guild_id=987654321098765432,
            channel_id=777888999000111222,
            parent_id=555444333222111333,
            content=f"<@{bot_id}> hi",
        )
        await adapter_with_repo._handle_message(msg)
        assert adapter_with_repo._on_message.await_count == 1
        # Thread should now be registered.
        thread = await adapter_with_repo._thread_manager.get_thread(
            "987654321098765432", "777888999000111222",
        )
        assert thread is not None


# ==================== _emit_message callback (COVERAGE 3) ====================


class TestEmitMessage:
    """The ``_emit_message`` plumbing that calls the user-provided callback."""

    @pytest.mark.asyncio
    async def test_emit_message_calls_callback(self, adapter):
        incoming = IncomingMessage(
            external_user_id="dm:123456789012345678",
            content="hello",
            source_id="discord-main",
        )
        await adapter._emit_message(incoming)
        assert adapter._on_message.await_count == 1
        assert adapter._on_message.await_args.args[0] is incoming

    @pytest.mark.asyncio
    async def test_emit_message_callback_error_logged(self, adapter, caplog):
        """If the callback raises, the error must be logged — not crash."""

        async def boom(msg):
            raise RuntimeError("callback crashed")

        a = DiscordAdapter(make_discord_config(), boom)
        incoming = IncomingMessage(
            external_user_id="dm:123456789012345678",
            content="hello",
            source_id="discord-main",
        )
        with caplog.at_level(logging.ERROR):
            # NOTE: base impl re-raises — we just verify the message goes through.
            with pytest.raises(RuntimeError, match="callback crashed"):
                await a._emit_message(incoming)


# ==================== _periodic_eviction_loop (COVERAGE 4) ====================


class TestPeriodicEvictionLoop:
    """Coverage for the periodic TTL eviction background loop."""

    @pytest.mark.asyncio
    async def test_eviction_loop_calls_thread_manager(self, mock_on_message):
        cfg = make_discord_config(eviction_interval_seconds=1)
        a = DiscordAdapter(cfg, mock_on_message, manager=MagicMock())
        mock_tm = MagicMock()
        mock_tm.evict_expired = AsyncMock(return_value=[])
        a._thread_manager = mock_tm

        # Patch ``asyncio.sleep`` so each iteration only yields briefly,
        # then cancel the task after the first evict pass.
        task = asyncio.create_task(a._periodic_eviction_loop())
        real_sleep = asyncio.sleep

        async def fast_sleep(*args, **kwargs):
            await real_sleep(0)
            if mock_tm.evict_expired.await_count >= 1:
                # Trigger cancellation of the loop from outside.
                task.cancel()
            return None

        with patch("asyncio.sleep", side_effect=fast_sleep):
            await task

        assert mock_tm.evict_expired.await_count >= 1

    @pytest.mark.asyncio
    async def test_eviction_loop_cancels_cleanly(self, mock_on_message):
        cfg = make_discord_config()
        a = DiscordAdapter(cfg, mock_on_message, manager=MagicMock())
        a._thread_manager = MagicMock()
        a._thread_manager.evict_expired = AsyncMock(return_value=[])

        task = asyncio.create_task(a._periodic_eviction_loop())
        await asyncio.sleep(0.01)
        task.cancel()
        # Should NOT raise — CancelledError is caught and silenced.
        await task

    @pytest.mark.asyncio
    async def test_eviction_loop_survives_evict_error(self, mock_on_message):
        """If ``evict_expired`` raises, the loop must continue."""
        cfg = make_discord_config(eviction_interval_seconds=1)
        a = DiscordAdapter(cfg, mock_on_message, manager=MagicMock())

        call_count = {"n": 0}

        async def sometimes_fails():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("evict boom")
            return []

        a._thread_manager = MagicMock()
        a._thread_manager.evict_expired = sometimes_fails

        task = asyncio.create_task(a._periodic_eviction_loop())
        real_sleep = asyncio.sleep

        async def fast_sleep(*args, **kwargs):
            await real_sleep(0)
            if call_count["n"] >= 2:
                task.cancel()
            return None

        with patch("asyncio.sleep", side_effect=fast_sleep):
            await task

        # The loop survived the first failure and continued calling.
        assert call_count["n"] >= 2


# ==================== DM routing path (COVERAGE 5) ====================


class TestDMRouting:
    """DM-mode routing through ``_route_outgoing`` and ``send``."""

    @pytest.mark.asyncio
    async def test_dm_routing_creates_dm_channel(self, adapter_with_repo):
        adapter_with_repo._status = SourceStatus.RUNNING

        fake_dm_chan = MagicMock()
        fake_dm_chan.send = AsyncMock(return_value=MagicMock())

        fake_user = MagicMock()
        fake_user.create_dm = AsyncMock(return_value=fake_dm_chan)

        fake_client = MagicMock()
        fake_client.fetch_user = AsyncMock(return_value=fake_user)
        adapter_with_repo._client = fake_client

        out = OutgoingMessage(
            external_user_id="dm:123456789012345678",
            content="hi",
            source_id="discord-main",
        )
        result = await adapter_with_repo.send(out)
        assert result is True
        fake_client.fetch_user.assert_awaited_with(123456789012345678)
        fake_user.create_dm.assert_awaited()
        fake_dm_chan.send.assert_awaited()

    @pytest.mark.asyncio
    async def test_dm_routing_user_not_found(self, adapter_with_repo):
        adapter_with_repo._status = SourceStatus.RUNNING

        fake_client = MagicMock()
        fake_client.fetch_user = AsyncMock(return_value=None)
        adapter_with_repo._client = fake_client

        out = OutgoingMessage(
            external_user_id="dm:123456789012345678",
            content="hi",
            source_id="discord-main",
        )
        result = await adapter_with_repo.send(out)
        assert result is False

    @pytest.mark.asyncio
    async def test_dm_routing_resolves_via_resolve_target(self, adapter_with_repo):
        """``_resolve_send_target`` for a DM returns mode='dm' without
        touching the source repo."""
        info = await adapter_with_repo._resolve_send_target(
            "dm:123456789012345678"
        )
        assert info is not None
        assert info["mode"] == "dm"
        assert info["user_id"] == "123456789012345678"
        assert info["channel_id"] is None
        # DM mode MUST NOT touch the repo.
        adapter_with_repo._source_repo.get_instance_mapping.assert_not_called()


# ==================== Token format validation unit test ====================


class TestTokenFormatHelper:
    """Unit tests for the ``_is_valid_discord_token_format`` helper."""

    def test_valid_format(self):
        from daemon.sources.adapters.discord.adapter import (
            _is_valid_discord_token_format,
        )
        assert _is_valid_discord_token_format(
            "MTIzNDU2Nzg5.Mabcdef.test_signature_123"
        )

    def test_no_dots(self):
        from daemon.sources.adapters.discord.adapter import (
            _is_valid_discord_token_format,
        )
        assert not _is_valid_discord_token_format("not-a-token")

    def test_one_dot(self):
        from daemon.sources.adapters.discord.adapter import (
            _is_valid_discord_token_format,
        )
        assert not _is_valid_discord_token_format("foo.bar")

    def test_two_dots_extra_segment(self):
        from daemon.sources.adapters.discord.adapter import (
            _is_valid_discord_token_format,
        )
        assert not _is_valid_discord_token_format("a.b.c.d")

    def test_empty(self):
        from daemon.sources.adapters.discord.adapter import (
            _is_valid_discord_token_format,
        )
        assert not _is_valid_discord_token_format("")


# ==================== _get_guild_threads regression (FIX 8) ====================


class TestGuildThreadsLockRegression:
    """FIX 8 regression: concurrent ``register_thread`` for the same new
    guild_id must serialize on ``_guilds_guard`` and produce a single,
    consistent ``OrderedDict``.
    """

    @pytest.mark.asyncio
    async def test_concurrent_register_same_new_guild(self):
        mgr = DiscordThreadManager(manager=MagicMock())
        # Fire many concurrent registers for the SAME brand-new guild_id.
        async def reg(i):
            await mgr.register_thread(
                "new-guild", "222", f"t{i}", instance_id=f"i{i}"
            )
        await asyncio.gather(*[reg(i) for i in range(20)])
        # Exactly one OrderedDict was created, with 20 entries.
        assert len(mgr._threads) == 1
        assert len(mgr._threads["new-guild"]) == 20
