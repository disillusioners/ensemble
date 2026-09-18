"""
Tests for the source-config-edit-reload bugfix.

Bug class: daemon/sources/registry.py SourceRegistry.stop_adapter() did not
evict the adapter from ``self._adapters``. As a result, the router start path
(daemon/routers/sources.py:379-381) skipped fresh adapter construction because
``source_registry.get(source_id)`` returned the retained (stale) adapter
instance, and any construction-time private field (``_default_agent``,
``_agent``, ``_channel_require_mention``, ``_require_mention``, ...) stayed at
its pre-edit value.

Fix (seam 2): ``stop_adapter`` now evicts from ``_adapters`` so any subsequent
start rebuilds from the latest persisted config.

Coverage:
  (a) Slack: stop → edit (persist via repository) → start ⇒ started adapter
      uses NEW default_agent.
  (b) Discord: key-naming case (Discord reads ``"agent"`` not
      ``"default_agent"``); rebuild picks up edited value.
  (c) Generic registry-level case: post-start adapter object identity differs
      from pre-stop.
  (d) Safety for seam 2: ``list_adapters`` and ``get`` return correct values
      for STOPPED sources (the only consumers of an evicted adapter are
      production read paths; verified here that no read path regresses).
"""

from __future__ import annotations

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, Mock

from daemon.sources.base import (
    IncomingMessage,
    MessageSourceAdapter,
    SourceConfig,
    SourceStatus,
)
from daemon.sources.registry import SourceRegistry


# ==================== Local test adapter ====================


class _RecordingAdapter(MessageSourceAdapter):
    """Test adapter that records its construction-time config.

    Used to prove that two adapter instances constructed from different
    configs carry different captured fields, mirroring the production bug
    surface (SlackAdapter._default_agent, DiscordAdapter._default_agent,
    TelegramAdapter._default_agent, SchedulerAdapter._agent).
    """

    def __init__(self, config: SourceConfig, on_message):
        super().__init__(config, on_message)
        # Mirror Slack/Discord/Telegram capture pattern at adapter __init__.
        self._default_agent = config.config.get("default_agent", "ari")
        # Mirror Discord's KEY_AGENT trap ("agent" not "default_agent").
        self._discord_agent = config.config.get("agent", self._default_agent)

    async def start(self):
        self._status = SourceStatus.RUNNING

    async def stop(self):
        self._status = SourceStatus.STOPPED

    async def send(self, message):
        return True

    async def health_check(self):
        return True


def _make_config(
    source_id: str,
    source_type: str = "slack",
    default_agent: str = "ari",
    discord_agent: str | None = None,
) -> SourceConfig:
    """Build a SourceConfig with both Slack-style and Discord-style keys."""
    cfg = {"default_agent": default_agent}
    if discord_agent is not None:
        cfg["agent"] = discord_agent
    return SourceConfig(
        source_id=source_id,
        source_type=source_type,
        name=f"Test {source_type}",
        config=cfg,
        credentials={
            "bot_token": "xoxb-test-token",
            "app_token": "xapp-test-token",
        },
        enabled=True,
    )


# ==================== Shared fixtures ====================


@pytest.fixture
def mock_manager():
    """A mock InstanceManager wired with a permissive source_repo.

    The source_repo mocks are no-ops by default; tests override the methods
    they need to drive (e.g. ``get_source_config`` to simulate DB reads).
    """
    manager = MagicMock()
    mock_config = MagicMock()
    mock_config.agents.directory = "/default/agents"
    manager.config = mock_config

    repo = MagicMock()
    repo.check_and_mark_processed = MagicMock(return_value=False)
    repo.get_instance_mapping = MagicMock(return_value=None)
    repo.create_instance_mapping = MagicMock(return_value=MagicMock())
    repo.delete_instance_mapping = MagicMock()
    repo.list_source_configs = MagicMock(return_value=[])
    repo.update_source_status = MagicMock()
    repo.get_source_config = MagicMock(return_value=None)
    repo.update_source_config = MagicMock()
    manager._source_repo = repo
    return manager


# ==================== (c) Generic registry-level invariant ====================


@pytest.mark.asyncio
async def test_stop_adapter_evicts_adapter_from_registry_dict(mock_manager):
    """stop_adapter MUST remove the adapter from ``_adapters`` so the next
    start rebuilds from the latest DB config (this is the core invariant
    that closes the bug).
    """
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    config = _make_config("src-1")
    adapter = _RecordingAdapter(config, lambda msg: None)
    registry.register(adapter)

    # Sanity: registered.
    assert registry.get("src-1") is adapter

    await registry.stop_adapter("src-1")

    # Post-fix: adapter is evicted. Pre-fix: this assertion FAILS because
    # stop_adapter leaves the adapter in self._adapters.
    assert registry.get("src-1") is None, (
        "stop_adapter must evict the adapter so the next start rebuilds from "
        "the latest DB config (source-config-edit-reload bugfix)"
    )


@pytest.mark.asyncio
async def test_stop_then_register_new_adapter_does_not_raise_value_error(mock_manager):
    """After stop_adapter, registering a fresh adapter for the same source_id
    must NOT raise the ``already registered`` ValueError. This is the seam-2
    precondition that makes the router's start path (sources.py:381-422)
    work after an edit.
    """
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)

    adapter_v1 = _RecordingAdapter(_make_config("src-1"), lambda msg: None)
    registry.register(adapter_v1)

    await registry.stop_adapter("src-1")

    adapter_v2 = _RecordingAdapter(
        _make_config("src-1", default_agent="bob"), lambda msg: None
    )
    # Post-fix: register succeeds because the dict was evicted. Pre-fix: this
    # raises ValueError("Adapter already registered: src-1").
    registry.register(adapter_v2)

    assert registry.get("src-1") is adapter_v2


# ==================== (a) Slack: stop → edit → start uses NEW default_agent ====================


@pytest.mark.asyncio
async def test_slack_stop_edit_start_uses_new_default_agent(mock_manager):
    """End-to-end Slack case: register a Slack-shaped adapter, stop, register
    a fresh one with edited ``default_agent``, assert the post-edit adapter
    carries the edited value. Pre-fix this fails because the first adapter
    is retained and the second register raises ValueError.
    """
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)

    adapter_v1 = _RecordingAdapter(
        _make_config("slack-1", default_agent="ari"), lambda msg: None
    )
    registry.register(adapter_v1)
    assert adapter_v1._default_agent == "ari"

    await registry.stop_adapter("slack-1")

    # Simulate the user editing the source config via PATCH /sources/{id}
    # (which writes through manager._source_repository.update_source_config).
    # The next start reads fresh from the DB; here we just build a fresh
    # adapter from the edited config — exactly what
    # daemon/routers/sources.py:379-422 does after our seam-2 fix.
    adapter_v2 = _RecordingAdapter(
        _make_config("slack-1", default_agent="bob"), lambda msg: None
    )
    registry.register(adapter_v2)

    # Identity changed AND new default_agent is in effect.
    assert registry.get("slack-1") is adapter_v2
    assert registry.get("slack-1") is not adapter_v1
    assert registry.get("slack-1")._default_agent == "bob"


@pytest.mark.asyncio
async def test_slack_stop_then_start_via_registry_start_adapter_path(mock_manager):
    """Drive the actual ``start_adapter`` path used by the router. Pre-fix
    this asserts the wrong (stale) default_agent; post-fix the start path
    rebuilds from the latest DB config and the asserted agent is the new one.
    """
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    # Stub _run_adapter_safe so start_adapter doesn't try to actually start
    # the supervisor loop in unit tests.
    registry._run_adapter_safe = AsyncMock()  # type: ignore[method-assign]

    # First registration: DB has default_agent="ari".
    adapter_v1 = _RecordingAdapter(
        _make_config("slack-1", default_agent="ari"), lambda msg: None
    )
    registry.register(adapter_v1)
    await registry.start_adapter("slack-1")
    assert adapter_v1._default_agent == "ari"

    # User-initiated stop.
    await registry.stop_adapter("slack-1")
    # Clean up the stub supervisor task created by the mocked start_adapter.
    registry._supervisor_tasks.pop("slack-1", None)

    # After stop, registry.get is None (seam 2 invariant).
    assert registry.get("slack-1") is None

    # Simulate PATCH /sources/{id} → DB now has default_agent="bob".
    # Then the user POSTs /sources/{id}/start, which (post-fix) rebuilds.
    adapter_v2 = _RecordingAdapter(
        _make_config("slack-1", default_agent="bob"), lambda msg: None
    )
    registry.register(adapter_v2)  # would raise pre-fix
    await registry.start_adapter("slack-1")
    registry._supervisor_tasks.pop("slack-1", None)

    assert registry.get("slack-1") is adapter_v2
    assert registry.get("slack-1")._default_agent == "bob"


# ==================== (b) Discord key-naming case ====================


@pytest.mark.asyncio
async def test_discord_stop_edit_start_uses_new_agent_key(mock_manager):
    """Discord reads ``"agent"`` not ``"default_agent"`` (KEY_AGENT at
    daemon/sources/adapters/discord/adapter.py:117). The rebuild-from-DB
    path must pick up the edited ``"agent"`` value.
    """
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)

    adapter_v1 = _RecordingAdapter(
        _make_config("discord-1", discord_agent="ari"), lambda msg: None
    )
    registry.register(adapter_v1)
    # Mirrors DiscordAdapter.__init__ KEY_AGENT behavior.
    assert adapter_v1._discord_agent == "ari"

    await registry.stop_adapter("discord-1")
    assert registry.get("discord-1") is None

    adapter_v2 = _RecordingAdapter(
        _make_config("discord-1", discord_agent="carol"), lambda msg: None
    )
    registry.register(adapter_v2)

    assert registry.get("discord-1")._discord_agent == "carol"


# ==================== (d) Safety: no read path regresses for STOPPED sources ====================


@pytest.mark.asyncio
async def test_list_adapters_excludes_evicted_stopped_source(mock_manager):
    """After stop_adapter, ``list_adapters`` no longer reports the source.
    This is the documented seam-2 contract: stop = removed from the in-memory
    registry. Production status display does NOT depend on this (it reads
    from DB via ``manager._source_repository.list_source_configs`` at
    daemon/routers/sources.py:92), so the eviction is safe.
    """
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    config = _make_config("src-1")
    adapter = _RecordingAdapter(config, lambda msg: None)
    registry.register(adapter)

    assert len(registry.list_adapters()) == 1

    await registry.stop_adapter("src-1")

    # Post-fix: the stopped source is no longer in the in-memory adapter list.
    assert registry.list_adapters() == []
    # And get() also returns None (the precondition the router relies on).
    assert registry.get("src-1") is None


@pytest.mark.asyncio
async def test_get_after_stop_returns_none(mock_manager):
    """Direct getter contract: post-stop, get(source_id) is None. Multiple
    production paths (sources.py:329/379, webhooks.py:88, schedules.py:53/147
    /218/282) call get() and handle None gracefully (HTTPException 503 for
    webhooks; silent skip for schedules). The eviction preserves those
    contracts — a None reply is correct for a stopped source.
    """
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    config = _make_config("src-1")
    adapter = _RecordingAdapter(config, lambda msg: None)
    registry.register(adapter)

    assert registry.get("src-1") is adapter

    await registry.stop_adapter("src-1")

    assert registry.get("src-1") is None


# ==================== Scheduler lifecycle-exclusion (regression guard) ====================


@pytest.mark.asyncio
async def test_scheduler_stop_still_cleans_supervisor_and_persists_status(mock_manager):
    """Scheduler sources still go through stop_adapter (during shutdown via
    stop_all). After seam 2, scheduler stop_adapter must still:
      - cancel any supervisor task
      - persist status='stopped' to DB (when persist_status=True)
      - evict from _adapters (the new contract)
    """
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    repo = mock_manager._source_repo
    repo.update_source_status = MagicMock()

    config = SourceConfig(
        source_id="sched-1",
        source_type="scheduler",
        name="Test Scheduler",
        config={"interval_seconds": 3600, "agent": "ari", "message": "hello"},
        credentials={},
    )
    adapter = _RecordingAdapter(config, lambda msg: None)
    registry.register(adapter)

    # Pretend a supervisor task was running (boot path).
    fake_task = asyncio.create_task(asyncio.sleep(60))
    registry._supervisor_tasks["sched-1"] = fake_task
    registry._running["sched-1"] = True

    await registry.stop_adapter("sched-1")  # persist_status=True default

    # Persisted stopped status (API path).
    repo.update_source_status.assert_any_call("sched-1", "stopped")
    # Adapter evicted (new seam-2 invariant).
    assert registry.get("sched-1") is None
    # Supervisor task cancelled.
    assert fake_task.cancelled() or fake_task.done()


# ==================== Adapter-level invariant documents construction-time capture ====================


def test_slack_adapter_construction_reads_default_agent_at_init():
    """Construction-time capture: SlackAdapter reads ``default_agent`` from
    config at __init__ (daemon/sources/adapters/slack/adapter.py:98). The
    only way to change the value is to construct a NEW adapter — which is
    exactly what seam 2 enables on stop→start.
    """
    from daemon.sources.adapters.slack.adapter import SlackAdapter

    a = SlackAdapter(
        _make_config("slack-1", default_agent="ari"),
        lambda msg: None,
    )
    assert a._default_agent == "ari"

    b = SlackAdapter(
        _make_config("slack-1", default_agent="bob"),
        lambda msg: None,
    )
    assert b._default_agent == "bob"
    # Two distinct instances; mutating one does not affect the other.
    assert a is not b


def test_discord_adapter_construction_reads_agent_key_at_init():
    """Construction-time capture: DiscordAdapter reads ``"agent"`` (NOT
    ``"default_agent"``) at __init__ (daemon/sources/adapters/discord/adapter
    .py:117, :155). The only way to change the value is to construct a NEW
    adapter.
    """
    from daemon.sources.adapters.discord.adapter import DiscordAdapter

    a = DiscordAdapter(
        _make_config("discord-1", discord_agent="ari"),
        lambda msg: None,
    )
    assert a._default_agent == "ari"

    b = DiscordAdapter(
        _make_config("discord-1", discord_agent="carol"),
        lambda msg: None,
    )
    assert b._default_agent == "carol"


def test_telegram_adapter_construction_reads_default_agent_at_init():
    """Construction-time capture: TelegramAdapter reads ``default_agent``
    from config at __init__ (daemon/sources/adapters/telegram.py:94).
    """
    from daemon.sources.adapters.telegram import TelegramAdapter

    a = TelegramAdapter(
        _make_config("tg-1", default_agent="ari"),
        lambda msg: None,
    )
    assert a._default_agent == "ari"

    b = TelegramAdapter(
        _make_config("tg-1", default_agent="dave"),
        lambda msg: None,
    )
    assert b._default_agent == "dave"


# ==================== Router-level test: POST /sources/{id}/start after stop ====================


@pytest.mark.asyncio
async def test_router_start_source_uses_edited_config_after_stop(mock_manager):
    """End-to-end through the actual router path
    (daemon/routers/sources.py:343-444). Pre-fix this reuses the retained
    stale adapter; post-fix it builds a new adapter from the fresh DB read.
    """
    from fastapi import FastAPI
    import httpx
    from daemon.routers.sources import router

    # Wire a real registry into the mock manager.
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    registry._run_adapter_safe = AsyncMock()  # type: ignore[method-assign]
    mock_manager.source_registry = registry
    mock_manager.is_write_paused = False

    # Initial DB row: default_agent=ari.
    initial_row = Mock()
    initial_row.source_id = "slack-1"
    initial_row.source_type = "slack"
    initial_row.name = "Slack Test"
    initial_row.config = {"default_agent": "ari"}
    initial_row.credentials = None  # no credentials = no decrypt needed
    initial_row.enabled = True
    initial_row.autostart = True
    initial_row.status = "running"
    initial_row.error_message = None
    initial_row.created_at = "2026-01-01T00:00:00+00:00"
    initial_row.updated_at = "2026-01-01T00:00:00+00:00"

    # Pre-register an adapter as if the source had been running (post a
    # prior start). Use the real SlackAdapter so _default_agent capture is
    # production-faithful.
    from daemon.sources.adapters.slack.adapter import SlackAdapter

    initial_cfg = SourceConfig(
        source_id="slack-1",
        source_type="slack",
        name="Slack Test",
        config={"default_agent": "ari"},
        credentials={
            "bot_token": "xoxb-test-token",
            "app_token": "xapp-test-token",
        },
        enabled=True,
    )
    initial_adapter = SlackAdapter(initial_cfg, lambda msg: None)
    registry.register(initial_adapter)
    registry._supervisor_tasks["slack-1"] = asyncio.create_task(asyncio.sleep(60))
    registry._running["slack-1"] = True

    # Simulate the user editing default_agent via PATCH /sources/{id}:
    # update_source_config persists; the in-memory adapter stays stale.
    # credentials are stored encrypted in DB; the router path decrypts via
    # app.state.credential_manager. We pass a plain dict here and let the
    # router code path's decryption logic skip (it only decrypts when the
    # value is a str; a dict means "no encryption applied"). We also include
    # both bot_token and app_token so SlackAdapter construction succeeds.
    edited_row = Mock()
    edited_row.source_id = "slack-1"
    edited_row.source_type = "slack"
    edited_row.name = "Slack Test"
    edited_row.config = {"default_agent": "bob"}
    edited_row.credentials = {
        "bot_token": "xoxb-test-token",
        "app_token": "xapp-test-token",
    }
    edited_row.enabled = True
    edited_row.autostart = True
    edited_row.status = "stopped"
    edited_row.error_message = None
    edited_row.created_at = "2026-01-01T00:00:00+00:00"
    edited_row.updated_at = "2026-01-02T00:00:00+00:00"

    # DB returns the edited row on the next read.
    mock_manager._source_repository.get_source_config = MagicMock(
        return_value=edited_row
    )
    mock_manager._source_repository.update_source_status = MagicMock()
    # CredentialManager is None in this test; the router only touches it
    # if credentials are non-empty, which they are not here.

    # First call: user stops the source (POST /sources/{id}/stop).
    await registry.stop_adapter("slack-1")
    registry._supervisor_tasks.pop("slack-1", None)
    assert registry.get("slack-1") is None

    # Now wire up FastAPI and POST /sources/slack-1/start.
    app = FastAPI()
    app.include_router(router)
    app.state.manager = mock_manager
    # The router also touches app.state.credential_manager on the
    # create-source path; start_source does NOT (it only reads from DB).
    # Add a stub just in case future code paths change.
    app.state.credential_manager = MagicMock()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.post("/sources/slack-1/start")

    assert resp.status_code == 200, resp.text

    # The registered adapter must be the freshly-built one with bob.
    final_adapter = registry.get("slack-1")
    assert final_adapter is not None, (
        "After POST /sources/slack-1/start the registry must hold the "
        "freshly-built adapter (seam-2 invariant)."
    )
    assert final_adapter._default_agent == "bob", (
        f"Expected edited default_agent='bob' on the post-start adapter; "
        f"got {final_adapter._default_agent!r}. The router start path "
        f"rebuilt the adapter but used the stale config."
    )

    # Cleanup supervisor stub.
    registry._supervisor_tasks.pop("slack-1", None)
