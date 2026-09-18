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

    # First call: user stops the source (POST /sources/{id}/stop).
    await registry.stop_adapter("slack-1")
    registry._supervisor_tasks.pop("slack-1", None)
    assert registry.get("slack-1") is None

    # Now wire up FastAPI and POST /sources/slack-1/start.
    app = FastAPI()
    app.include_router(router)
    app.state.manager = mock_manager
    # start_source (sources.py:351) fetches credential_manager from app.state
    # but only calls ``.decrypt()`` when credentials are a str (the encrypted
    # form); the edited_row.credentials above is a dict (the plaintext shape
    # the SlackAdapter constructor expects), so the decrypt branch is skipped
    # and the stub below simply satisfies the fetch.
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


# ============================================================================
# Round-2 fixes: scheduler pause→resume regression + TOCTOU + boot-skip.
#
# Round-2 review of 28179686 found:
#  - CRITICAL regression: seam-2 (stop_adapter evicts) + the /schedules/{id}/start
#    route (schedules.py:281-288) interact badly. After stop, the registry has
#    no adapter; start_adapter returns False silently; the route then sets
#    ``status = adapter.status if adapter else None``; pydantic rejects
#    ``status=None`` (SourceActionResponse.status is required, SourceStatus
#    enum, daemon/models/source.py:181-183); HTTPException 500 on EVERY
#    same-session pause→resume of a scheduler.
#  - WARNING: TOCTOU persist window — ``self._adapters.pop(source_id, None)``
#    lands AFTER the awaited ``update_source_status`` (:563); a concurrent
#    ``/sources/{id}/start`` in that window sees the old adapter and starts
#    STALE config.
#
# Round-2 tests pin both regressions and also heal the resume-after-restart
# pre-existing breakage via the same rebuild branch (a stopped-at-boot
# scheduler can now be started via the route).
# ============================================================================


def _scheduler_row(
    source_id: str = "sched-1",
    agent: str = "./agents/developer",
    interval_seconds: int = 3600,
    status: str = "stopped",
):
    """Build a SQLModel-shaped Mock row for a scheduler source.

    Mirrors what ``manager._source_repository.get_source_config`` returns
    in production (the schedules.py router reads through this attribute).
    """
    row = Mock()
    row.source_id = source_id
    row.source_type = "scheduler"
    row.name = "Test Schedule"
    row.config = {
        "interval_seconds": interval_seconds,
        "agent": agent,
        "message": "Hello from scheduler",
        "instance_mode": "new_instance",
    }
    row.credentials = None
    row.enabled = True
    row.autostart = True
    row.status = status
    row.error_message = None
    row.created_at = "2026-01-01T00:00:00+00:00"
    row.updated_at = "2026-01-01T00:00:00+00:00"
    return row


class _SchedulerRecordingAdapter(MessageSourceAdapter):
    """Test adapter that mimics SchedulerAdapter's construction-time capture.

    Captures ``agent`` from config at __init__ (mirrors
    SchedulerAdapter._agent = scheduler_config.get("agent") at
    daemon/sources/adapters/scheduler.py:117). Exposes ``_get_next_trigger_time``
    because the schedules router does not require it but production schedulers
    provide it (start_schedule response uses adapter.status, not
    ``_get_next_trigger_time``).
    """

    _created_count: int = 0  # class-level: count constructions for assertions

    def __init__(self, config: SourceConfig, on_message):
        super().__init__(config, on_message)
        type(self)._created_count += 1
        self._agent = config.config.get("agent", "./agents/developer")
        self._interval_seconds = config.config.get("interval_seconds", 3600)

    async def start(self):
        self._status = SourceStatus.RUNNING

    async def stop(self):
        self._status = SourceStatus.STOPPED

    async def send(self, message):
        return True

    async def health_check(self):
        return True

    async def manual_trigger(self):
        return "exec-test"

    def _get_next_trigger_time(self):
        return None


# --- (CRITICAL) Scheduler pause→resume regression (RED at 28179686) ---


@pytest.mark.asyncio
async def test_scheduler_pause_resume_round_trip_regression(mock_manager):
    """CRITICAL regression pin: scheduler stop→start round-trip via the
    ``/schedules/{id}/stop`` and ``/schedules/{id}/start`` routes.

    Pre-fix chain (regression introduced by 28179686):

      1. POST /schedules/sched-1/stop → stop_adapter → evicts (registry.py:578)
         + persists status=stopped (:563).
      2. POST /schedules/sched-1/start → start_adapter at schedules.py:281 →
         ``registry.get(id) is None`` → returns False (registry.py:492-495).
      3. Route IGNORES the False return; falls through to
         ``adapter = registry.get(id); status = adapter.status if adapter else
         None`` → constructs ``SourceActionResponse(status=None, ...)``.
      4. SourceActionResponse.status is REQUIRED (non-Optional, enum, see
         daemon/models/source.py:181-183) → pydantic ValidationError → caught
         by the route's ``except Exception`` → HTTPException 500 with message
         "Failed to start scheduler: <validation error>".

    Post-fix: the route adds a rebuild branch when ``registry.get(id) is None``:
    load fresh config from ``_source_repository``, construct adapter via
    ``_create_adapter_from_config``, register, then call ``start_adapter`` and
    surface any False return as HTTPException 500. The response is now a valid
    SourceActionResponse(status=SourceStatus.running, ...).

    Round-2 fix choice: rebuild branch in the schedule router (reviewer's
    PREFERRED). Not registry self-construct — would change semantics for the
    other two ``start_adapter`` callers (sources.py:172 auto-start after create,
    sources.py:425 POST /sources/{id}/start) which are out of scope.
    """
    from fastapi import FastAPI
    import httpx
    from daemon.routers.schedules import router as schedules_router

    # Reset class counter so this test sees its own constructions only.
    _SchedulerRecordingAdapter._created_count = 0

    # Wire a real registry into the mock manager.
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    # Avoid the actual supervisor loop (we just want to prove the rebuild).
    registry._run_adapter_safe = AsyncMock()  # type: ignore[method-assign]
    # Force the rebuild path to use _SchedulerRecordingAdapter instead of the
    # real SchedulerAdapter (which needs JobQueueService + instance_repo + a
    # configured manager). The registry's _create_adapter_from_config uses
    # source_type-based dispatch; we monkey-patch it to return our adapter
    # for "scheduler" so the test exercises the rebuild branch faithfully.
    real_create = registry._create_adapter_from_config

    async def _create_stub(config):
        if config.source_type == "scheduler":
            async def on_message(msg):
                pass
            return _SchedulerRecordingAdapter(config, on_message)
        return await real_create(config)

    registry._create_adapter_from_config = _create_stub  # type: ignore[method-assign]

    mock_manager.source_registry = registry
    mock_manager.is_write_paused = False

    # DB row (current persisted state).
    row = _scheduler_row()
    mock_manager._source_repository.get_source_config = MagicMock(return_value=row)
    mock_manager._source_repository.update_source_status = MagicMock()

    # Pre-register an adapter as if the scheduler had been running.
    initial_adapter = _SchedulerRecordingAdapter(
        SourceConfig(
            source_id="sched-1",
            source_type="scheduler",
            name="Test Schedule",
            config={
                "interval_seconds": 3600,
                "agent": "./agents/developer",
                "message": "Hello",
                "instance_mode": "new_instance",
            },
            credentials={},
            enabled=True,
        ),
        lambda msg: None,
    )
    registry.register(initial_adapter)
    registry._supervisor_tasks["sched-1"] = asyncio.create_task(asyncio.sleep(60))
    registry._running["sched-1"] = True
    assert registry.get("sched-1") is initial_adapter

    # Wire FastAPI app with both routers (schedules depends on app.state.manager).
    app = FastAPI()
    app.include_router(schedules_router)
    app.state.manager = mock_manager
    # Schedules router doesn't read app.state.credential_manager, but mirror the
    # other test's hygiene to keep the seam surface honest.
    app.state.credential_manager = MagicMock()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # Step 1: stop the scheduler (POST /schedules/{id}/stop).
        stop_resp = await ac.post("/schedules/sched-1/stop")
        assert stop_resp.status_code == 200, stop_resp.text
        assert stop_resp.json()["status"] == "stopped"

        # Seam-2 invariant: adapter evicted from registry.
        registry._supervisor_tasks.pop("sched-1", None)
        assert registry.get("sched-1") is None

        # Step 2: start the scheduler (POST /schedules/{id}/start).
        start_resp = await ac.post("/schedules/sched-1/start")

    # CRITICAL assertion: this MUST be 200 with a valid SourceStatus. Pre-fix
    # this returns 500 because start_adapter returns False (registry.get is
    # None → schedules.py:282 → status=None → pydantic rejects).
    assert start_resp.status_code == 200, (
        f"scheduler pause→resume MUST return 200, got {start_resp.status_code}: "
        f"{start_resp.text!r}. Regression introduced by 28179686's seam-2 fix: "
        f"stop_adapter evicts the scheduler adapter, but start_schedule at "
        f"schedules.py:281 does not rebuild, so start_adapter returns False, "
        f"and the route constructs SourceActionResponse(status=None, ...) which "
        f"pydantic rejects (SourceActionResponse.status is required, see "
        f"daemon/models/source.py:181-183)."
    )

    data = start_resp.json()
    # Round-2 invariant: status field MUST be a valid SourceStatus enum value,
    # never null. Pre-fix the route returned status=null → ValidationError →
    # caught and re-raised as 500.
    assert data.get("status") is not None, (
        f"start_schedule response status is None — regression: schedules.py:282 "
        f"returns adapter.status if adapter else None, but with seam-2 evict "
        f"the adapter is gone, so status becomes None and pydantic rejects "
        f"SourceActionResponse(status=None, ...). Got response: {data!r}"
    )
    assert data["status"] in {"stopped", "starting", "running", "error"}, (
        f"start_schedule response status is not a valid SourceStatus enum: "
        f"{data.get('status')!r}"
    )
    assert data["source_id"] == "sched-1"
    assert "started successfully" in data["message"]

    # The route must have rebuilt the adapter from the DB config.
    final_adapter = registry.get("sched-1")
    assert final_adapter is not None, (
        "After POST /schedules/sched-1/start the registry MUST hold the "
        "freshly-built scheduler adapter (round-2 rebuild branch)."
    )
    assert final_adapter is not initial_adapter, (
        "After stop→start the adapter MUST be a fresh object, not the "
        "pre-stop retained one (seam-2 invariant)."
    )
    assert final_adapter._agent == "./agents/developer", (
        f"Freshly-built scheduler must carry the persisted agent; "
        f"got {final_adapter._agent!r}"
    )

    # Persisted status MUST include the start sequence (mirror what
    # start_all/start_adapter do). With the supervisor loop mocked (so the
    # scheduler doesn't actually start cron schedules), ``start_adapter``
    # persists STARTING synchronously; ``_run_adapter_safe`` would normally
    # persist RUNNING after the adapter's start() succeeds, but we never
    # reach it in the test. Asserting "starting" is the deterministic pin
    # the rebuild branch MUST honor (the route calls start_adapter, which
    # must persist STARTING before returning). The registry uses
    # ``self._source_repo`` (the fixture attr); the router uses
    # ``manager._source_repository`` — they are different mocks in this
    # test. Asserting on the registry's repo (the source of the persist).
    persisted_calls = [
        c for c in mock_manager._source_repo.update_source_status.call_args_list
    ]
    persisted_targets = {(c.args[0], c.args[1] if len(c.args) > 1 else None) for c in persisted_calls}
    assert ("sched-1", "starting") in persisted_targets, (
        f"After start_schedule via the rebuild branch, status='starting' MUST be "
        f"persisted (start_adapter at registry.py:504 persists STARTING before "
        f"returning). Persisted calls: {persisted_calls!r}"
    )

    # Exactly one rebuild construction (the second construction, after the
    # pre-registration). Pre-fix the rebuild never happens (count stays at 1);
    # post-fix we observe one fresh adapter built by the route, on top of
    # the pre-registered one (count == 2).
    assert _SchedulerRecordingAdapter._created_count == 2, (
        f"Expected pre-register + 1 rebuild construction (count=2); "
        f"got {_SchedulerRecordingAdapter._created_count}. Pre-fix the "
        f"route does NOT construct a fresh adapter (it just calls "
        f"start_adapter and lets it fail), so the count stays at 1."
    )

    # Cleanup the stub supervisor task.
    registry._supervisor_tasks.pop("sched-1", None)


# --- (CRITICAL bonus) Resume-after-restart rebuild (boot-skip case) ---


@pytest.mark.asyncio
async def test_scheduler_start_after_boot_skip_rebuilds_adapter(mock_manager):
    """Resume-after-restart pre-existing breakage is healed by the round-2
    rebuild branch: a scheduler that was 'stopped' at boot (boot-skip at
    registry.py:201-203) is NOT constructed during ``start_all``. The user
    must POST /schedules/{id}/start to bring it up. Pre-fix the route returned
    HTTP 500 (same ValidationError chain as pause→resume); post-fix the route
    loads the fresh DB row, constructs the adapter, registers, and starts.
    """
    from fastapi import FastAPI
    import httpx
    from daemon.routers.schedules import router as schedules_router

    _SchedulerRecordingAdapter._created_count = 0

    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    registry._run_adapter_safe = AsyncMock()  # type: ignore[method-assign]

    real_create = registry._create_adapter_from_config

    async def _create_stub(config):
        if config.source_type == "scheduler":
            async def on_message(msg):
                pass
            return _SchedulerRecordingAdapter(config, on_message)
        return await real_create(config)

    registry._create_adapter_from_config = _create_stub  # type: ignore[method-assign]

    mock_manager.source_registry = registry
    mock_manager.is_write_paused = False

    # The DB row a fresh boot-skip would NOT have constructed: status=stopped,
    # interval_seconds=3600, agent=./agents/developer.
    row = _scheduler_row()
    mock_manager._source_repository.get_source_config = MagicMock(return_value=row)
    mock_manager._source_repository.update_source_status = MagicMock()

    # Note: registry._adapters is EMPTY (boot-skip never constructed). Pre-fix
    # this still returns 500; post-fix the rebuild branch heals it.
    assert registry.get("sched-1") is None
    assert "sched-1" not in registry._adapters

    app = FastAPI()
    app.include_router(schedules_router)
    app.state.manager = mock_manager
    app.state.credential_manager = MagicMock()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.post("/schedules/sched-1/start")

    assert resp.status_code == 200, (
        f"Resume-after-restart (boot-skip case) MUST return 200, got "
        f"{resp.status_code}: {resp.text!r}. Round-2 rebuild branch should "
        f"heal this case for free: the scheduler was stopped at boot (boot-skip "
        f"at registry.py:201-203), so no adapter was constructed during "
        f"start_all. POST /schedules/sched-1/start must rebuild from the DB "
        f"row and start."
    )

    data = resp.json()
    assert data.get("status") is not None
    assert data["status"] in {"stopped", "starting", "running", "error"}
    assert registry.get("sched-1") is not None
    assert _SchedulerRecordingAdapter._created_count == 1

    registry._supervisor_tasks.pop("sched-1", None)


# --- (WARNING) TOCTOU persist window ordering pin ---


@pytest.mark.asyncio
async def test_stop_adapter_evicts_before_persisting_status(mock_manager):
    """TOCTOU ordering pin: stop_adapter MUST evict the adapter from
    ``_adapters`` BEFORE awaiting ``update_source_status``. Otherwise a
    concurrent /sources/{id}/start in that window would ``registry.get(id) is
    not None`` and start the STALE adapter (the exact bug class seam-2 was
    meant to close). Verified by hooking ``update_source_status`` with a
    side_effect that asserts ``source_id not in registry._adapters`` at the
    call site.
    """
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)

    config = _make_config("src-toctou")
    adapter = _RecordingAdapter(config, lambda msg: None)
    registry.register(adapter)
    registry._supervisor_tasks["src-toctou"] = asyncio.create_task(asyncio.sleep(60))
    registry._running["src-toctou"] = True

    observed: dict[str, object] = {}

    def _assert_evicted_at_persist(source_id, status, *args, **kwargs):
        # This side_effect fires during the await of update_source_status.
        # At THIS point the adapter MUST already be evicted (the ordering
        # contract). Pre-fix the pop happens AFTER the await, so this
        # assertion FAILS — capturing the regression.
        observed["adapter_present_at_persist"] = (
            registry._adapters.get(source_id) is not None
        )
        observed["source_id"] = source_id
        observed["status"] = status

    mock_manager._source_repo.update_source_status = MagicMock(
        side_effect=_assert_evicted_at_persist
    )

    await registry.stop_adapter("src-toctou")
    registry._supervisor_tasks.pop("src-toctou", None)

    # The side_effect MUST have observed the adapter already evicted.
    assert observed.get("adapter_present_at_persist") is False, (
        f"TOCTOU regression: stop_adapter awaited update_source_status while "
        f"the adapter was still in registry._adapters. A concurrent "
        f"/sources/{{id}}/start in this window would see the stale adapter "
        f"and start it (the exact bug class seam-2 is meant to close). "
        f"Observed: {observed!r}"
    )
    # And after stop, the adapter is gone (sanity).
    assert registry.get("src-toctou") is None


# --- Idempotency pin: start_schedule when already running still works ---


@pytest.mark.asyncio
async def test_scheduler_start_when_adapter_already_running_is_idempotent(
    mock_manager,
):
    """The rebuild branch MUST NOT regress the idempotent-already-running
    case: if the adapter is already in the registry (no evict happened), the
    route should just call start_adapter and return success. Mirrors the
    existing ``test_start_schedule_idempotent_already_running`` semantics but
    exercises the rebuilt path with a real registry.
    """
    from fastapi import FastAPI
    import httpx
    from daemon.routers.schedules import router as schedules_router

    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    registry._run_adapter_safe = AsyncMock()  # type: ignore[method-assign]

    real_create = registry._create_adapter_from_config

    async def _create_stub(config):
        if config.source_type == "scheduler":
            async def on_message(msg):
                pass
            return _SchedulerRecordingAdapter(config, on_message)
        return await real_create(config)

    registry._create_adapter_from_config = _create_stub  # type: ignore[method-assign]
    mock_manager.source_registry = registry
    mock_manager.is_write_paused = False

    row = _scheduler_row(status="running")
    mock_manager._source_repository.get_source_config = MagicMock(return_value=row)
    mock_manager._source_repository.update_source_status = MagicMock()

    # Pre-register the adapter (already running — no stop happened).
    adapter = _SchedulerRecordingAdapter(
        SourceConfig(
            source_id="sched-1",
            source_type="scheduler",
            name="Test Schedule",
            config={
                "interval_seconds": 3600,
                "agent": "./agents/developer",
                "message": "Hello",
                "instance_mode": "new_instance",
            },
            credentials={},
            enabled=True,
        ),
        lambda msg: None,
    )
    adapter._status = SourceStatus.RUNNING
    registry.register(adapter)
    registry._supervisor_tasks["sched-1"] = asyncio.create_task(asyncio.sleep(60))
    registry._running["sched-1"] = True

    app = FastAPI()
    app.include_router(schedules_router)
    app.state.manager = mock_manager
    app.state.credential_manager = MagicMock()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.post("/schedules/sched-1/start")

    # Already-running case: idempotent, 200 with valid status, no rebuild.
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("status") is not None
    assert data["status"] in {"stopped", "starting", "running", "error"}
    # The same adapter object is reused (no rebuild).
    assert registry.get("sched-1") is adapter

    registry._supervisor_tasks.pop("sched-1", None)
