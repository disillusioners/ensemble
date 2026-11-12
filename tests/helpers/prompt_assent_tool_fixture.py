"""Shared fakes for the prompt-assent Phase-2 TOOL tests (task 2.10).

The tool gate reads DAEMON-OWNED evidence — the factor-2 user-origin
window (``manager._user_origin_windows``), the window's MESSAGE ROW
(``manager._queue_repository.get(message_id)``), the pending item
(``manager._prompt_version_repo``) and the token store (the manager's
engine). These fakes provide exactly those surfaces — nothing else — so
a test can prove a gate leg without booting the daemon.

The window is the SAME object shape the promote gate reads
(``daemon/tools/upgrade_tools.py:3388-3391``): ``{source, message_id,
stamped_at, expires_at}``.
"""

from __future__ import annotations

from typing import Any, Optional

#: The relay instance (the ari instance that calls the tool).
RELAY_INSTANCE_ID = "inst-relay-1"
#: The daemon-minted id of the triggering human message row.
MESSAGE_ID = "msg-relay-1"
#: The daemon-side source id of the chat adapter.
SOURCE_ID = "discord"
#: The channel the human wrote in.
CHANNEL_ID = "999888777"
#: The human's platform user id.
USER_ID = "112233445566"


class FakeMessageRow:
    """A message-queue row — the window's trigger evidence."""

    def __init__(
        self,
        *,
        content: str = "",
        discord_meta: Optional[dict[str, Any]] = None,
        message_id: str = MESSAGE_ID,
        instance_id: str = RELAY_INSTANCE_ID,
        source: str = SOURCE_ID,
    ) -> None:
        self.message_id = message_id
        self.instance_id = instance_id
        self.content = content
        self.source = source
        meta: dict[str, Any] = {"agent": "ari"}
        if discord_meta is not None:
            meta["discord"] = discord_meta
        self.message_metadata = meta


class FakeQueueRepository:
    """The manager's queue repository — one row, addressed by id."""

    def __init__(self, row: Optional[FakeMessageRow] = None) -> None:
        self._row = row

    def get(self, message_id: str) -> Optional[FakeMessageRow]:
        row = self._row
        if row is None or row.message_id != message_id:
            return None
        return row


class FakeChatAdapter:
    """A registered chat adapter — ``source_type`` is what arms the gate."""

    source_type = "discord"


class FakeSourceRegistry:
    """The sources registry — the UNFORKED classifier's lookup."""

    def get(self, source_id: str) -> Optional[FakeChatAdapter]:
        if source_id == SOURCE_ID:
            return FakeChatAdapter()
        return None


class FakeManager:
    """The manager surface the tool gate reads.

    ONLY the attributes the gate touches are present — a missing handle
    is a refusal, not a crash.
    """

    def __init__(
        self,
        *,
        engine: Any = None,
        pv_repo: Any = None,
        row: Optional[FakeMessageRow] = None,
        windows: Optional[dict[str, dict]] = None,
        last_stamps: Optional[dict[str, dict]] = None,
        source_registry: Any = None,
        service: Any = None,
    ) -> None:
        self._engine = engine
        self._prompt_version_repo = pv_repo
        self._prompt_version_service = service
        self._queue_repository = FakeQueueRepository(row)
        self._user_origin_windows: dict[str, dict] = windows if windows is not None else {}
        self._user_origin_last_stamp: dict[str, dict] = (
            last_stamps if last_stamps is not None else {}
        )
        self.source_registry = (
            source_registry if source_registry is not None else FakeSourceRegistry()
        )


def build_window(
    *,
    source: str = SOURCE_ID,
    message_id: str = MESSAGE_ID,
    age_s: int = 0,
    ttl_s: int = 600,
) -> dict[str, Any]:
    """A user-origin window in the promote gate's exact shape.

    ``age_s`` backdates ``stamped_at`` (the FRESHNESS leg — D-6/J-2,
    600 s) and ``ttl_s`` sets the TTL from the stamp (the EXPIRY leg).
    """
    from daemon.tools.upgrade_journal import iso_plus, now_iso

    now = now_iso()
    stamped = iso_plus(now, -int(age_s))
    expires = iso_plus(stamped, int(ttl_s))
    return {
        "source": source,
        "message_id": message_id,
        "stamped_at": stamped,
        "expires_at": expires,
    }


def build_discord_meta(
    *,
    user_id: Optional[str] = USER_ID,
    channel_id: Optional[str] = CHANNEL_ID,
    is_dm: Any = True,
    is_group_dm: Any = False,
    guild_id: Optional[str] = None,
    channel_type: str = "dm",
    parent_channel_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> dict[str, Any]:
    """The nested ``metadata["discord"]`` dict the adapter builds.

    Phase-1.5 thread cells (D-9 / M-12 / M-13): a thread carries
    ``channel_type="thread"``, its OWN ``channel_id`` and the parent's
    id in ``parent_channel_id`` — the adapter's exact key set
    (``daemon/sources/adapters/discord/adapter.py:1080-1092``). The two
    new keys are ADDITIVE-ONLY: they appear in the dict ONLY when the
    caller passes them, so every pre-existing caller's meta dict is
    byte-identical to before this parameter existed.
    """
    meta: dict[str, Any] = {
        "guild_id": guild_id,
        "channel_id": channel_id,
        "channel_type": channel_type,
        "user_id": user_id,
        "user_name": "The Emperor",
        "message_id": "discord-platform-msg-1",
        "is_dm": is_dm,
        "is_group_dm": is_group_dm,
    }
    if parent_channel_id is not None:
        meta["parent_channel_id"] = parent_channel_id
    if thread_id is not None:
        meta["thread_id"] = thread_id
    return meta


def build_tools(
    manager: Any,
    *,
    agent_id: str = "ari",
    instance_id: str = RELAY_INSTANCE_ID,
) -> dict[str, Any]:
    """The tool map — ``{name: tool}`` (the house invocation shape)."""
    from daemon.tools.prompt_assent import create_prompt_assent_tools

    return {
        tool.name: tool
        for tool in create_prompt_assent_tools(
            manager,
            current_instance_id=instance_id,
            agent_id=agent_id,
        )
    }
