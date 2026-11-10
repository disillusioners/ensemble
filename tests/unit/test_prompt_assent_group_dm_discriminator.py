"""Phase-1 K-3 group-DM discriminator tests (task 1.12).

Covers phase1-plan task 1.12 acceptance:

* a 1:1 DM and a Discord GROUP DM classify DISTINCTLY through the real
  adapter build site (``_normalize_incoming`` → nested ``discord_meta``);
* an ABSENT discriminator ⇒ NOT private (fail-closed, directly asserted
  through ``is_private_assent_channel``);
* an UNPARSEABLE discriminator ⇒ not private;
* the discriminator is ADDITIVE metadata — no schema change — and the
  adapter's existing behavior is otherwise unchanged (guild channels
  keep ``is_dm=False``; the pre-existing keys are untouched).

⚠ PRE-SHIP RUNTIME CONFIRMATION (flagged for the tester): a LIVE probe
must show a real 1:1 DM and a real group DM classifying distinctly —
this file proves the classification LOGIC against duck-typed channel
shapes; the live discord.py behavior (DMChannel.recipient vs
GroupChannel.recipients) is the tester's runtime probe (a miss is
bounded to availability, never authority — fail-closed-absent).
"""

from __future__ import annotations

import pytest

from daemon.repositories.prompt_assent.mint_context import (
    is_private_assent_channel,
)
from daemon.sources.adapters.discord.adapter import (
    DiscordAdapter,
    _classify_dm_channel,
)
from tests.helpers.prompt_assent_fixture import sha256_hex  # noqa: F401


class _User:
    def __init__(self, uid: str, name: str):
        self.id = uid
        self.name = name
        self.display_name = name


class _DMChannel:
    """duck-typed discord.py DMChannel: singular ``recipient``."""

    def __init__(self, user: _User, channel_id: str = "999888777"):
        self.recipient = user
        self.id = channel_id
        self.name = None
        self.parent_id = None


class _GroupChannel:
    """duck-typed discord.py GroupChannel: ``recipients`` collection."""

    def __init__(self, users, channel_id: str = "grp-1"):
        self.recipients = list(users)
        self.id = channel_id
        self.name = "the conclave"
        self.parent_id = None


class _GuildChannel:
    def __init__(self, channel_id: str = "guild-chan-1"):
        self.id = channel_id
        self.name = "general"
        self.parent_id = None


class _Guild:
    def __init__(self, gid: str = "555000111"):
        self.id = gid
        self.name = "The Court"


def _message(channel, author, guild=None):
    class _Msg:
        pass

    m = _Msg()
    m.author = author
    m.channel = channel
    m.guild = guild
    m.content = "hello the court"
    m.attachments = []
    m.reference = None
    m.id = 424242424242
    return m


def _adapter():
    """A REAL adapter (same construction path as ``tests/test_discord_adapter.py``).

    ``_normalize_incoming`` needs the instance attrs the real
    ``__init__`` sets (``_strip_llm_artifact_tags_enabled``,
    ``_default_agent``, ``source_id``) — a bare ``__new__`` stub would
    prove nothing about the real build site.
    """
    from daemon.sources.base import SourceConfig

    config = SourceConfig(
        source_id="my-discord-bot",
        source_type="discord",
        name="Test Discord Bot",
        config={"agent": "ari"},
        credentials={"bot_token": "AA.BB.CC"},
        enabled=True,
    )
    return DiscordAdapter(config, lambda *a, **k: None)


class TestGroupDmDiscriminator:
    def test_one_to_one_dm_and_group_dm_classify_distinctly(self):
        user = _User("112233445566", "The Emperor")
        intruder = _User("666655554444", "The Pretender")

        dm = _adapter()._normalize_incoming(_message(_DMChannel(user), user))
        group = _adapter()._normalize_incoming(
            _message(_GroupChannel([user, intruder]), user)
        )
        assert dm is not None and group is not None
        dm_meta = dm.metadata["discord"]
        group_meta = group.metadata["discord"]
        # BOTH read is_dm=True today (guild absence) — the OLD defect.
        assert dm_meta["is_dm"] is True
        assert group_meta["is_dm"] is True
        # The NEW discriminator separates them.
        assert dm_meta["is_group_dm"] is False
        assert group_meta["is_group_dm"] is True
        # The lane's private check: 1:1 private, group NOT private.
        assert is_private_assent_channel(dm_meta) is True
        assert is_private_assent_channel(group_meta) is False

    def test_guild_channel_not_private_and_untouched(self):
        user = _User("112233445566", "The Emperor")
        msg = _adapter()._normalize_incoming(
            _message(_GuildChannel(), user, guild=_Guild())
        )
        meta = msg.metadata["discord"]
        assert meta["is_dm"] is False
        assert meta["is_group_dm"] is False  # truthful: not a group DM
        assert is_private_assent_channel(meta) is False
        # Additive: the pre-existing keys are all still present.
        for key in (
            "guild_id", "guild_name", "channel_id", "channel_name",
            "channel_type", "user_id", "user_name", "message_id", "is_dm",
        ):
            assert key in meta, f"additive discipline broken: {key} vanished"

    def test_absent_discriminator_is_not_private(self):
        """Fail-closed, DIRECTLY asserted: an older row without the key
        cannot mint or approve until the discriminator ships."""
        assert is_private_assent_channel({"is_dm": True}) is False
        assert is_private_assent_channel({}) is False
        assert is_private_assent_channel(None) is False

    def test_unparseable_discriminator_is_not_private(self):
        assert is_private_assent_channel(
            {"is_dm": True, "is_group_dm": None}
        ) is False
        assert is_private_assent_channel(
            {"is_dm": True, "is_group_dm": "no"}
        ) is False
        assert is_private_assent_channel(
            {"is_dm": True, "is_group_dm": 0}
        ) is False  # int 0 is not the literal False — fail-closed
        assert is_private_assent_channel(
            {"is_dm": "yes", "is_group_dm": False}
        ) is False

    def test_unclassifiable_channel_shape_yields_none(self):
        assert _classify_dm_channel(object()) is None

    def test_no_schema_change(self):
        """Additive metadata only: the adapter change touches no model
        and no migration."""
        from pathlib import Path

        mig_files = sorted(
            Path("daemon/migrations/versions").glob("*.sql")
        )
        discord_migs = [
            f.name for f in mig_files if "discord" in f.name.lower()
        ]
        assert discord_migs == [], "no schema change rides this lane"
        adapter_src = Path(
            "daemon/sources/adapters/discord/adapter.py"
        ).read_text(encoding="utf-8")
        # The discriminator lands in the nested metadata dict only.
        assert '"is_group_dm": is_group_dm,' in adapter_src
        assert "discord_meta" in adapter_src
