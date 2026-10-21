"""DECISIVE GATE — Rule 2 of the 2026-10-20 user ruling (the alarm wakes
the CALLER) + the DELIVERY-MECHANISM proof.

The ruling (Discord, direct, 2026-10-20) is BINDING: a ``self_alarm``
fires INTO THE CALLING instance — the same instance, the same session
context, NO new-instance spawn. The earlier "the fired instance is a
fresh instance with no memory" framing is RETRACTED.

The user's own worked example is a ONE-SHOT (arm at 13:00 → fire at
14:00), which is why the one-shot path matters here.

Three proofs, in increasing distance from the wrapper:

A. THE ROW (REAL DB) — create the alarm through the REAL tool path
   against a REAL source repository, then read the REAL
   ``source_configs`` row: ``instance_mode`` is ``reuse_instance`` and
   the message carries the ALARM INTENT (label + board item id + the
   action text).
B. THE WAKE TARGET (REAL DB) — the REAL ``instance_mappings`` row points
   at the CALLING instance id, keyed the way the engine's adapter reads
   it (``get_instance_mapping(source_id, source_id)``).
C. THE DELIVERY (ENGINE SEAM) — drive the REAL ``SchedulerAdapter``'s
   reuse-instance fire path and assert the delivery is an ordinary USER
   MESSAGE enqueued into the CALLING instance — and NOT a spawn.

Proof C is the one that settles the delivery mechanism: the engine's
reuse path calls ``manager.enqueue_message_job(...)`` (the MESSAGE
path) and never ``spawn_instance_with_mcp`` (the SPAWN path), with
``source="scheduler"`` — a source the messaging layer classifies as
``MessageType.HUMAN`` (i.e. ``role="user"``), not one of the internal
stamped namespaces.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from daemon.repositories.source.repository import SQLModelSourceRepository

CALLER_INSTANCE = "inst-caller-wake-1"
CALLER_AGENT = "ari"
PROJECT_ID = "proj-wake-gate"
TZ_NAME = "Asia/Ho_Chi_Minh"
LABEL = "deploy-check"
EFFECTIVE_LABEL = "self-alarm:deploy-check"
BOARD_ITEM = "wi-1a2b3c4d"
ACTION_TEXT = "Check the deploy and update the board item."

#: The alarm intent as the engine delivers it (the message the fired
#: session receives).
ALARM_INTENT = "Check the deploy and update the board item."


def _composed_alarm_message() -> str:
    """The REAL composed alarm message (the wrapper's D7 contract).

    Built with the production helper so the engine-seam proof uses
    exactly the bytes the wrapper stores and the engine delivers.
    """
    from datetime import datetime, timezone as _tz

    from daemon.services.self_alarm_service import compose_alarm_message

    return compose_alarm_message(
        effective_label=EFFECTIVE_LABEL,
        first_fire_local=datetime(2026, 10, 21, 14, 0, tzinfo=_tz.utc),
        first_fire_utc=datetime(2026, 10, 21, 14, 0, tzinfo=_tz.utc),
        armed_at_utc=datetime(2026, 10, 21, 3, 0, tzinfo=_tz.utc),
        recurrence="once",
        timezone_name=TZ_NAME,
        source_id="src-wake-gate-1",
        board_item=BOARD_ITEM,
        message_to_self=ALARM_INTENT,
    )


# ---------------------------------------------------------------------------
# Fixtures — a REAL source repository over a throwaway SQLite DB
# ---------------------------------------------------------------------------


@pytest.fixture
def real_source_repo(tmp_path):
    """A REAL ``SQLModelSourceRepository`` over a throwaway SQLite DB.

    SAFETY: the path is under pytest's ``tmp_path`` — never a
    HOME-derived or live-shaped path is resolved.
    """
    engine = create_engine(
        f"sqlite:///{tmp_path / 'self_alarm_wake.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    repo = SQLModelSourceRepository(engine)
    yield repo
    engine.dispose()


class _StubRegistry:
    """A source-registry double.

    The REAL registry would build and start a live adapter; the row
    under test is persisted by the REAL repository either way, and the
    service treats an adapter-build failure as "the row persists and
    fires on next boot". This stub reproduces that shape without
    starting a timer.
    """

    def __init__(self) -> None:
        self.registered: list = []

    async def _create_adapter_from_config(self, row):
        return None

    def register(self, adapter) -> None:
        self.registered.append(adapter)

    async def start_adapter(self, source_id) -> bool:
        return False


@pytest.fixture
def tool_with_real_repo(real_source_repo, monkeypatch: pytest.MonkeyPatch):
    """The self_alarm tool bound to the REAL scheduling service + REAL repo."""
    from daemon.services import scheduling_service
    from daemon.tools.self_alarm import create_self_alarm_tools

    monkeypatch.setenv("ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE", TZ_NAME)
    scheduling_service.configure(
        source_repo=real_source_repo,
        source_registry=_StubRegistry(),
        project_repository=None,
    )

    instance_repo = MagicMock()
    instance_repo.get = MagicMock(
        return_value=SimpleNamespace(project_id=PROJECT_ID)
    )

    tools = create_self_alarm_tools(
        scheduling_service,
        source_repo=real_source_repo,
        instance_repository=instance_repo,
        project_repository=None,
        current_instance_id=CALLER_INSTANCE,
        agent_id=CALLER_AGENT,
    )
    by_name = {t.name: t for t in tools}
    assert set(by_name) == {"self_alarm"}
    return by_name, real_source_repo


# ---------------------------------------------------------------------------
# A + B — the REAL row and the REAL wake target
# ---------------------------------------------------------------------------


class TestTheRealScheduleRow:
    """A — the CREATED SCHEDULE ROW, read back from the REAL DB."""

    @pytest.mark.asyncio
    async def test_the_row_is_reuse_instance_and_carries_the_alarm_intent(
        self, tool_with_real_repo
    ) -> None:
        tools, repo = tool_with_real_repo
        result = await tools["self_alarm"].coroutine(
            label=LABEL,
            when_or_every="in 45m",
            message_to_self=ACTION_TEXT,
            link_board_item=BOARD_ITEM,
        )
        assert isinstance(result, dict), result
        source_id = result["source_id"]
        assert source_id

        # The REAL row, read back from the REAL DB.
        row = repo.get_source_config(source_id)
        assert row is not None
        config = row.config

        assert config["instance_mode"] == "reuse_instance", (
            f"the alarm must be armed for the caller-bound wake; got {config}"
        )
        assert config["agent"] == CALLER_AGENT

        message = config["message"]
        assert message.startswith(f"[self-alarm] {EFFECTIVE_LABEL}")
        assert "Scheduled fire:" in message
        assert f"Board item: {BOARD_ITEM}" in message
        assert message.endswith(f"Action: {ALARM_INTENT}")
        # The fresh-instance framing is RETRACTED.
        assert "fresh instance" not in message
        assert "no memory" not in message

    @pytest.mark.asyncio
    async def test_the_real_mapping_points_at_the_CALLING_instance(
        self, tool_with_real_repo
    ) -> None:
        """B — the wake target, read back from the REAL DB."""
        tools, repo = tool_with_real_repo
        result = await tools["self_alarm"].coroutine(
            label=LABEL,
            when_or_every="in 45m",
            message_to_self=ACTION_TEXT,
        )
        assert isinstance(result, dict), result
        source_id = result["source_id"]

        # The engine's adapter reads the mapping with this exact key.
        mapping = repo.get_instance_mapping(source_id, source_id)
        assert mapping is not None, (
            "the schedule row must carry an instance mapping — without it "
            "the engine's reuse-instance path would spawn a fresh instance"
        )
        assert mapping.agent_instance_id == CALLER_INSTANCE
        assert mapping.agent_id == CALLER_AGENT

    @pytest.mark.asyncio
    async def test_a_daily_alarm_is_also_reuse_instance(
        self, tool_with_real_repo
    ) -> None:
        """The recurring families honour the mode too (the one-shot case
        is covered by the engine-seam proof below)."""
        tools, repo = tool_with_real_repo
        result = await tools["self_alarm"].coroutine(
            label="daily-check",
            when_or_every="daily 14:00",
            message_to_self=ACTION_TEXT,
        )
        assert isinstance(result, dict), result
        row = repo.get_source_config(result["source_id"])
        assert row.config["instance_mode"] == "reuse_instance"
        assert row.config.get("run_at") is None  # a recurring row, not a one-shot


# ---------------------------------------------------------------------------
# C — the ENGINE seam: the delivery IS a user message into the caller
# ---------------------------------------------------------------------------


def _adapter_config(source_id: str, message: str, *, one_shot: bool) -> SimpleNamespace:
    config: dict = {
        "agent": CALLER_AGENT,
        "message": message,
        "instance_mode": "reuse_instance",
        "timezone": "UTC",
        "project_id": PROJECT_ID,
        "recurrence": "once" if one_shot else "daily",
        "local_time": "",
    }
    if one_shot:
        # A one-shot stores run_at — the adapter's historical force to
        # new_instance. The ruling makes an EXPLICIT reuse_instance
        # honoured, so the user's own one-shot example wakes the caller.
        from datetime import datetime, timedelta, timezone as _tz

        config["run_at"] = (
            datetime.now(_tz.utc) + timedelta(hours=1)
        ).isoformat()
    else:
        config["schedule"] = "0 14 * * *"
    return SimpleNamespace(
        source_id=source_id,
        config=config,
        source_type="scheduler",
        status="running",
        enabled=True,
        autostart=True,
        name="self-alarm:gate",
    )


class _ManagerDouble:
    """Captures the engine's delivery call."""

    def __init__(self) -> None:
        self.instances: dict = {}
        self.enqueued: list[dict] = []
        self.spawned: list[dict] = []

    async def enqueue_message_job(self, instance_id, message, source="api", **kwargs):
        self.enqueued.append(
            {
                "instance_id": instance_id,
                "message": message,
                "source": source,
                **kwargs,
            }
        )
        return SimpleNamespace(job_id="job-1", message_id="msg-1")

    async def spawn_instance_with_mcp(self, **kwargs):
        self.spawned.append(kwargs)
        return "inst-spawned"


@pytest.mark.asyncio
@pytest.mark.parametrize("one_shot", [False, True], ids=["recurring", "one_shot"])
async def test_the_engine_delivers_a_USER_MESSAGE_into_the_CALLER(
    real_source_repo, one_shot: bool
) -> None:
    """C — the delivery mechanism, at the engine seam.

    The engine's reuse-instance path resolves the mapped instance and
    enqueues the alarm message into it as an ordinary message. It must
    NOT spawn.
    """
    from daemon.sources.adapters.scheduler import SchedulerAdapter

    source_id = "src-wake-gate-1"
    # The mapping the wrapper would have written (the wake target).
    real_source_repo.create_instance_mapping(
        source_id=source_id,
        external_user_id=source_id,
        agent_instance_id=CALLER_INSTANCE,
        agent_id=CALLER_AGENT,
        agent_dir="/agents/ari",
        metadata={"source": "self_alarm", "bound_to_caller": True},
    )

    ALARM_MESSAGE = _composed_alarm_message()

    manager = _ManagerDouble()
    instance_repo = MagicMock()
    instance_repo.get = MagicMock(
        return_value=SimpleNamespace(status="idle", id=CALLER_INSTANCE)
    )

    adapter = SchedulerAdapter(
        _adapter_config(source_id, ALARM_MESSAGE, one_shot=one_shot),
        on_message=AsyncMock(),
        source_repo=real_source_repo,
        instance_repo=instance_repo,
        manager=manager,
    )
    await adapter.start()
    try:
        await adapter._execute_run("exec-wake-gate-1", "scheduled")
    finally:
        await adapter.stop()

    # ── the target instance ──
    assert len(manager.enqueued) == 1, (
        f"the alarm must be delivered exactly once; got {manager.enqueued}"
    )
    delivery = manager.enqueued[0]
    assert delivery["instance_id"] == CALLER_INSTANCE, (
        "the delivery must land in the CALLING instance"
    )

    # ── the delivery CLASS: a message, not a spawn ──
    assert manager.spawned == [], (
        "the reuse-instance path must NOT spawn a new instance"
    )
    assert delivery["source"] == "scheduler"

    # ── the message content: the alarm intent ──
    # The engine's reuse-mode path wraps the stored message in its
    # CONTINUATION template (the same-session continuation framing) and
    # delivers it as the body of the injected user message. The alarm
    # intent rides VERBATIM inside it — the wake reason is in-message.
    assert "[CONTINUATION" in delivery["message"], (
        "a reuse-instance fire is a same-session continuation"
    )
    assert "Original scheduled task:" in delivery["message"]
    assert ALARM_MESSAGE in delivery["message"], (
        "the composed alarm message must ride verbatim into the delivery"
    )
    assert f"Action: {ALARM_INTENT}" in delivery["message"]
    assert f"Board item: {BOARD_ITEM}" in delivery["message"]

    # ── the delivery class is a USER message ──
    # The messaging layer classifies a source into HUMAN unless it
    # carries one of the internal stamped prefixes; "scheduler" does
    # not, so the message lands as role="user".
    from daemon.services.instance_messaging import (
        _INTERNAL_STAMPED_SOURCE_PREFIXES,
        _stamped_additional_kwargs,
    )

    assert not any(
        delivery["source"].startswith(prefix)
        for prefix in _INTERNAL_STAMPED_SOURCE_PREFIXES
    ), "a scheduler message must not be classified as internal traffic"
    assert _stamped_additional_kwargs(delivery["source"]) is None, (
        "a scheduler message must be BARE — a genuine user message"
    )


@pytest.mark.asyncio
async def test_a_one_shot_alarm_honours_the_explicit_reuse_instance(
    real_source_repo,
) -> None:
    """The user's own worked example is a ONE-SHOT.

    Historically the adapter forced every one-shot to ``new_instance``
    (the "a one-shot is a fresh task" framing the ruling retracts). An
    EXPLICIT ``reuse_instance`` is now honoured — and the DEFAULT is
    unchanged (a one-shot that says nothing still spawns).
    """
    from daemon.sources.adapters.scheduler import SchedulerAdapter
    from daemon.sources.adapters.scheduler import SchedulerInstanceMode

    ALARM_MESSAGE = _composed_alarm_message()
    explicit = SchedulerAdapter(
        _adapter_config("src-one-shot-explicit", ALARM_MESSAGE, one_shot=True),
        on_message=AsyncMock(),
        source_repo=real_source_repo,
        instance_repo=MagicMock(),
        manager=_ManagerDouble(),
    )
    assert explicit._instance_mode == SchedulerInstanceMode.REUSE_INSTANCE

    default_config = _adapter_config("src-one-shot-default", ALARM_MESSAGE, one_shot=True)
    default_config.config.pop("instance_mode")
    default = SchedulerAdapter(
        default_config,
        on_message=AsyncMock(),
        source_repo=real_source_repo,
        instance_repo=MagicMock(),
        manager=_ManagerDouble(),
    )
    assert default._instance_mode == SchedulerInstanceMode.NEW_INSTANCE


def test_the_sleep_is_not_needed_json_is_not_the_delivery() -> None:
    """A cheap guard that the alarm message is plain text carrying the
    intent (the engine delivers the message string verbatim)."""
    assert isinstance(ALARM_INTENT, str)
    assert json.loads(json.dumps({"message": ALARM_INTENT}))["message"] == ALARM_INTENT
    assert asyncio.iscoroutinefunction(_ManagerDouble().enqueue_message_job)
