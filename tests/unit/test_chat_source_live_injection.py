"""Chat-source live-turn message injection routing (Phase 6).

Pins the new ``daemon/sources/registry.py`` ``_handle_message`` routing
branch — ``feature/chat-source-live-injection`` (2026-09-19). When a
chat-source message (telegram:/slack:/discord:) arrives for a target
instance that is ``running`` with a live graph consumer AND the payload
is text-only, deliver via the RAM-FIFO injection lane
(``manager.set_injection``) so it lands in the agent's CURRENT turn —
exactly like HTTP ``POST /messages``.

Routing decisions pinned:

  * **RUNNING + live graph + text-only** → ``set_injection`` called
    (with provenance ``source`` + ``echo_id``); ``enqueue_message_job``
    NOT called; typing indicator NOT fired.
  * **RUNNING but graphless** → durable ``enqueue_message_job``
    fallthrough (DEFECT-A stranding guard; never strand a RAM-FIFO entry
    on a graphless target).
  * **IDLE / PAUSED / terminal (COMPLETED/ERROR/FAILED/TERMINATED)** →
    durable ``enqueue_message_job`` unchanged.
  * **Image-bearing** → durable enqueue even when RUNNING+live-graph
    (image-bearing FIFO entries are out of scope; the FIFO schema is
    text-only).
  * **Metadata-bearing** (non-empty ``msg.metadata`` dict) → durable
    enqueue even when RUNNING+live-graph.

Mock discipline: ``manager.set_injection`` and
``manager.enqueue_message_job`` are mocked at the **facade** level —
the test does NOT touch the InstanceMessagingService seam. The
mocks' signatures match the real facade methods (sync
``set_injection(instance_id, content, source=None, echo_id=None)``;
async ``enqueue_message_job(instance_id, message, source, priority,
images, metadata)``). This is the bug class the Facade-Forwarding
Discipline guards (``tests/unit/test_manager_enqueue_message_work_id_required.py``,
``tests/integration/test_job_driven_enqueue_work_id_facade.py``)
target; since this change introduces no new kwargs on either facade
method, no facade-forwarding test is required.

Companion of the cross-lane DEFECT-A pins in
``tests/unit/test_graphless_running_durable_dispatch.py`` — the chat
ingest path is the 4th target of the stranding guard.
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.sources.base import IncomingMessage
from daemon.sources.registry import SourceRegistry


# ---------------------------------------------------------------------------
# Helpers — local fixtures (no harness module needed)
# ---------------------------------------------------------------------------


def _build_registry_with_manager(manager: MagicMock) -> SourceRegistry:
    """Build a ``SourceRegistry`` with the given pre-stubbed ``manager``.

    The source repo only needs ``check_and_mark_processed`` to return
    ``False`` (no duplicate) for these tests.
    """
    mock_source_repo = MagicMock()
    mock_source_repo.check_and_mark_processed = MagicMock(return_value=False)
    return SourceRegistry(mock_source_repo, manager)


def _stub_manager(
    *,
    status: str | None = "idle",
    has_live_graph: bool = False,
) -> MagicMock:
    """Build a MagicMock manager with the facade methods stubbed for routing.

    Defaults match the "durable enqueue" path so tests that don't
    override ``status`` / ``has_live_graph`` exercise the legacy
    non-injection shape.
    """
    manager = MagicMock()
    mock_config = MagicMock()
    mock_config.agents.directory = "/default/agents"
    manager.config = mock_config

    manager.get_instance_info = MagicMock(
        return_value={"status": status} if status else {}
    )
    manager.has_live_graph_task = MagicMock(return_value=has_live_graph)
    manager.set_injection = MagicMock(
        return_value={"content": "", "timestamp": "2026-09-19T00:00:00+00:00"}
    )
    manager.enqueue_message_job = AsyncMock(
        return_value=MagicMock(
            message_id="msg-test-id",
            instance_id="instance-123",
            status="queued",
            job_id="job-test-id",
        )
    )
    return manager


def _stub_mapper(instance_id: str = "instance-123"):
    """Patch ``InstanceMapper.get_or_create_instance`` to return ``instance_id``."""
    mock_mapper_instance = MagicMock()
    mock_mapper_instance.get_or_create_instance = AsyncMock(
        return_value=instance_id
    )
    return mock_mapper_instance


# ---------------------------------------------------------------------------
# Mock-signature pin — guards against drift in the real facade methods.
# ---------------------------------------------------------------------------


class TestFacadeSignaturePin:
    """Mock-signature discipline: the mocks' signatures MUST match the
    real facade methods or this test file silently mocks the wrong
    shape (the classic AsyncMock + inspect.getsource vacuous-test trap
    that ``test_manager_enqueue_message_work_id_required.py`` is
    designed to catch for the InstanceManager facade).
    """

    def test_set_injection_real_signature_matches_mock_assumption(self):
        """``manager.set_injection`` is a SYNC method accepting
        ``(instance_id, content, source=None, echo_id=None)``.
        """
        from daemon.manager import InstanceManager

        sig = inspect.signature(InstanceManager.set_injection)
        params = list(sig.parameters.keys())
        # First two are positional (self, instance_id); the next three
        # are content, source, echo_id. Drop ``self``.
        assert params[:4] == [
            "self",
            "instance_id",
            "content",
            "source",
        ], (
            f"set_injection signature drift: {params[:4]} — update mock "
            f"if the facade added/renamed kwargs"
        )
        # echo_id is the 5th param.
        assert params[4] == "echo_id", (
            f"set_injection 5th param drift: {params[4]} — update mock"
        )
        # Must NOT be async — it's a RAM-FIFO append (no I/O).
        assert not asyncio.iscoroutinefunction(InstanceManager.set_injection), (
            "set_injection became async — registry mock would lose its "
            "MagicMock return_value semantics (AsyncMock would be required)"
        )

    def test_has_live_graph_task_real_signature_matches_mock_assumption(self):
        """``manager.has_live_graph_task`` is SYNC and takes ``instance_id``."""
        from daemon.manager import InstanceManager

        sig = inspect.signature(InstanceManager.has_live_graph_task)
        params = list(sig.parameters.keys())
        assert params[:2] == ["self", "instance_id"], (
            f"has_live_graph_task signature drift: {params[:2]}"
        )
        assert not asyncio.iscoroutinefunction(
            InstanceManager.has_live_graph_task
        ), (
            "has_live_graph_task became async — registry mock would "
            "require AsyncMock"
        )

    def test_get_instance_info_real_signature_matches_mock_assumption(self):
        """``manager.get_instance_info`` is SYNC and returns a ``dict``."""
        from daemon.manager import InstanceManager

        sig = inspect.signature(InstanceManager.get_instance_info)
        params = list(sig.parameters.keys())
        assert params[:2] == ["self", "instance_id"], (
            f"get_instance_info signature drift: {params[:2]}"
        )
        assert not asyncio.iscoroutinefunction(
            InstanceManager.get_instance_info
        ), (
            "get_instance_info became async — registry mock would "
            "require AsyncMock"
        )


# ---------------------------------------------------------------------------
# Core routing — RUNNING + live graph + text-only path
# ---------------------------------------------------------------------------


class TestRunningLiveGraphInjection:
    """RUNNING + live graph + text-only → RAM-FIFO injection lane."""

    @pytest.mark.asyncio
    async def test_set_injection_called_enqueue_not_called(self):
        """Happy path: text message on a RUNNING+live-graph instance
        routes to ``set_injection``; ``enqueue_message_job`` is NEVER
        called (no durable rows created).
        """
        manager = _stub_manager(status="running", has_live_graph=True)
        registry = _build_registry_with_manager(manager)
        mock_mapper_instance = _stub_mapper()
        mock_agent_registry = MagicMock()
        mock_agent = MagicMock()
        mock_agent.path = "/default/agents"
        mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
        mock_agent_registry.get = MagicMock(return_value=mock_agent)

        msg = IncomingMessage(
            external_user_id="user-alice",
            content="hello from telegram",
            source_id="telegram",
        )

        with patch(
            "daemon.sources.registry.InstanceMapper",
            return_value=mock_mapper_instance,
        ), patch(
            "daemon.sources.mapper.get_registry",
            return_value=mock_agent_registry,
        ):
            await registry._handle_message("telegram", msg)

        manager.set_injection.assert_called_once()
        manager.enqueue_message_job.assert_not_called()
        manager.has_live_graph_task.assert_called_once_with("instance-123")
        manager.get_instance_info.assert_called_once_with("instance-123")

    @pytest.mark.asyncio
    async def test_threads_echo_id_and_source(self):
        """The injection call threads BOTH ``source`` (chat provenance
        ``"{source_id}:{external_user_id}"``) AND a fresh ``echo_id``
        (uuid4 str) into ``set_injection``.
        """
        manager = _stub_manager(status="running", has_live_graph=True)
        registry = _build_registry_with_manager(manager)
        mock_mapper_instance = _stub_mapper()
        mock_agent_registry = MagicMock()
        mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
        mock_agent_registry.get = MagicMock(
            return_value=MagicMock(path="/default/agents")
        )

        msg = IncomingMessage(
            external_user_id="user-alice",
            content="hi",
            source_id="slack",
        )

        with patch(
            "daemon.sources.registry.InstanceMapper",
            return_value=mock_mapper_instance,
        ), patch(
            "daemon.sources.mapper.get_registry",
            return_value=mock_agent_registry,
        ):
            await registry._handle_message("slack", msg)

        manager.set_injection.assert_called_once()
        kwargs = manager.set_injection.call_args.kwargs
        # Positional: instance_id, content
        args = manager.set_injection.call_args.args
        assert args[0] == "instance-123"
        assert args[1] == "hi"
        # Kwargs: source + echo_id
        assert kwargs["source"] == "slack:user-alice", (
            f"chat provenance wrong: {kwargs.get('source')!r}"
        )
        assert "echo_id" in kwargs, "echo_id not threaded"
        echo_id = kwargs["echo_id"]
        assert isinstance(echo_id, str), f"echo_id not a str: {type(echo_id)}"
        # uuid4 string parseable back to UUID
        import uuid as _uuid

        parsed = _uuid.UUID(echo_id)
        assert str(parsed) == echo_id, "echo_id not a valid uuid4 string"

    @pytest.mark.asyncio
    async def test_distinct_messages_get_distinct_echo_ids(self):
        """Two consecutive injections on the same instance MUST mint
        distinct ``echo_id`` values (message-display-latency Phase 1
        relies on the same id appearing on POST-time echo + drain-time
        re-emit — collisions would collapse the FE bubble).
        """
        manager = _stub_manager(status="running", has_live_graph=True)
        registry = _build_registry_with_manager(manager)
        mock_mapper_instance = _stub_mapper()
        mock_agent_registry = MagicMock()
        mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
        mock_agent_registry.get = MagicMock(
            return_value=MagicMock(path="/default/agents")
        )

        msg_a = IncomingMessage(
            external_user_id="alice",
            content="first",
            source_id="telegram",
        )
        msg_b = IncomingMessage(
            external_user_id="alice",
            content="second",
            source_id="telegram",
        )

        with patch(
            "daemon.sources.registry.InstanceMapper",
            return_value=mock_mapper_instance,
        ), patch(
            "daemon.sources.mapper.get_registry",
            return_value=mock_agent_registry,
        ):
            await registry._handle_message("telegram", msg_a)
            await registry._handle_message("telegram", msg_b)

        assert manager.set_injection.call_count == 2
        ids = [
            c.kwargs["echo_id"]
            for c in manager.set_injection.call_args_list
        ]
        assert len(set(ids)) == 2, f"echo_ids collided: {ids}"

    @pytest.mark.asyncio
    async def test_discord_source_threading(self):
        """Regression pin: ``discord:`` source_id threads the same way
        as ``telegram:`` / ``slack:`` (all three are chat-source lanes
        after the chat-source-worker-lane Phase 3 wiring).
        """
        manager = _stub_manager(status="running", has_live_graph=True)
        registry = _build_registry_with_manager(manager)
        mock_mapper_instance = _stub_mapper()
        mock_agent_registry = MagicMock()
        mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
        mock_agent_registry.get = MagicMock(
            return_value=MagicMock(path="/default/agents")
        )

        msg = IncomingMessage(
            external_user_id="discord-user-42",
            content="hello from discord",
            source_id="discord",
        )

        with patch(
            "daemon.sources.registry.InstanceMapper",
            return_value=mock_mapper_instance,
        ), patch(
            "daemon.sources.mapper.get_registry",
            return_value=mock_agent_registry,
        ):
            await registry._handle_message("discord", msg)

        manager.set_injection.assert_called_once()
        assert (
            manager.set_injection.call_args.kwargs["source"]
            == "discord:discord-user-42"
        )


# ---------------------------------------------------------------------------
# DEFECT-A stranding guard — graphless RUNNING → durable enqueue
# ---------------------------------------------------------------------------


class TestRunningGraphlessDurableFallback:
    """RUNNING + graphless (DEFECT-A) → durable enqueue; never strand a
    RAM-FIFO entry on a graphless target.
    """

    @pytest.mark.asyncio
    async def test_graphless_running_falls_through_to_enqueue(self):
        """A RUNNING instance with NO live graph consumer MUST take
        the durable ``enqueue_message_job`` path. ``set_injection``
        MUST NOT be called (injecting here would strand the FIFO entry
        forever).
        """
        manager = _stub_manager(status="running", has_live_graph=False)
        registry = _build_registry_with_manager(manager)
        mock_mapper_instance = _stub_mapper()
        mock_agent_registry = MagicMock()
        mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
        mock_agent_registry.get = MagicMock(
            return_value=MagicMock(path="/default/agents")
        )

        msg = IncomingMessage(
            external_user_id="alice",
            content="hi",
            source_id="telegram",
        )

        with patch(
            "daemon.sources.registry.InstanceMapper",
            return_value=mock_mapper_instance,
        ), patch(
            "daemon.sources.mapper.get_registry",
            return_value=mock_agent_registry,
        ):
            await registry._handle_message("telegram", msg)

        manager.enqueue_message_job.assert_awaited_once()
        manager.set_injection.assert_not_called()
        manager.has_live_graph_task.assert_called_once_with("instance-123")


# ---------------------------------------------------------------------------
# Non-running statuses → durable enqueue (no injection for non-running)
# ---------------------------------------------------------------------------


class TestNonRunningStatusDurableFallback:
    """Statuses outside ``INJECTION_ELIGIBLE_STATUSES`` (``{"running"}``)
    fall through to durable enqueue. ``has_live_graph_task`` is
    NOT consulted (the status gate short-circuits first).
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status",
        ["idle", "paused", "completed", "error", "failed", "terminated"],
    )
    async def test_status_other_than_running_falls_through(self, status):
        """Negative pin: non-running statuses do NOT enter the
        injection branch regardless of ``has_live_graph_task``.
        """
        manager = _stub_manager(status=status, has_live_graph=True)
        registry = _build_registry_with_manager(manager)
        mock_mapper_instance = _stub_mapper()
        mock_agent_registry = MagicMock()
        mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
        mock_agent_registry.get = MagicMock(
            return_value=MagicMock(path="/default/agents")
        )

        msg = IncomingMessage(
            external_user_id="alice",
            content="hi",
            source_id="telegram",
        )

        with patch(
            "daemon.sources.registry.InstanceMapper",
            return_value=mock_mapper_instance,
        ), patch(
            "daemon.sources.mapper.get_registry",
            return_value=mock_agent_registry,
        ):
            await registry._handle_message("telegram", msg)

        manager.enqueue_message_job.assert_awaited_once()
        manager.set_injection.assert_not_called()


# ---------------------------------------------------------------------------
# Rich payloads → durable enqueue even when RUNNING+live-graph
# ---------------------------------------------------------------------------


class TestRichPayloadDurableFallback:
    """Images and non-empty metadata ALWAYS take the durable lane
    (``set_injection`` is text-only; its signature accepts ``content``,
    ``source``, ``echo_id`` and nothing else).
    """

    @pytest.mark.asyncio
    async def test_image_bearing_falls_through_even_when_live(self):
        """A message with ``images`` on a RUNNING+live-graph target
        MUST still take the durable enqueue path.
        """
        manager = _stub_manager(status="running", has_live_graph=True)
        registry = _build_registry_with_manager(manager)
        mock_mapper_instance = _stub_mapper()
        mock_agent_registry = MagicMock()
        mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
        mock_agent_registry.get = MagicMock(
            return_value=MagicMock(path="/default/agents")
        )

        msg = IncomingMessage(
            external_user_id="alice",
            content="see attached",
            source_id="telegram",
            images=["https://example.com/photo.jpg"],
        )

        with patch(
            "daemon.sources.registry.InstanceMapper",
            return_value=mock_mapper_instance,
        ), patch(
            "daemon.sources.mapper.get_registry",
            return_value=mock_agent_registry,
        ):
            await registry._handle_message("telegram", msg)

        manager.enqueue_message_job.assert_awaited_once()
        manager.set_injection.assert_not_called()
        # images must thread through to the durable enqueue unchanged
        kwargs = manager.enqueue_message_job.call_args.kwargs
        assert kwargs["images"] == ["https://example.com/photo.jpg"]

    @pytest.mark.asyncio
    async def test_metadata_bearing_falls_through_even_when_live(self):
        """A message with non-empty ``metadata`` on a RUNNING+live-graph
        target MUST still take the durable enqueue path. Empty ``{}``
        metadata is text-only and stays injectable.
        """
        manager = _stub_manager(status="running", has_live_graph=True)
        registry = _build_registry_with_manager(manager)
        mock_mapper_instance = _stub_mapper()
        mock_agent_registry = MagicMock()
        mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
        mock_agent_registry.get = MagicMock(
            return_value=MagicMock(path="/default/agents")
        )

        msg = IncomingMessage(
            external_user_id="alice",
            content="with metadata",
            source_id="slack",
            metadata={
                "slack_channel_id": "C123",
                "slack_thread_ts": "1700000000.000100",
            },
        )

        with patch(
            "daemon.sources.registry.InstanceMapper",
            return_value=mock_mapper_instance,
        ), patch(
            "daemon.sources.mapper.get_registry",
            return_value=mock_agent_registry,
        ):
            await registry._handle_message("slack", msg)

        manager.enqueue_message_job.assert_awaited_once()
        manager.set_injection.assert_not_called()


# ---------------------------------------------------------------------------
# Empty / whitespace-only content → durable enqueue
# ---------------------------------------------------------------------------


class TestEmptyContentDurableFallback:
    """Empty / whitespace-only content MUST take the durable lane
    (defensive — the chat-source path does not S4-validate ``message.content``
    like HTTP does, but a blank injection would still produce a wasted turn).
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("content", ["", "   ", "\n\t  \n"])
    async def test_empty_or_whitespace_content_falls_through(self, content):
        manager = _stub_manager(status="running", has_live_graph=True)
        registry = _build_registry_with_manager(manager)
        mock_mapper_instance = _stub_mapper()
        mock_agent_registry = MagicMock()
        mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
        mock_agent_registry.get = MagicMock(
            return_value=MagicMock(path="/default/agents")
        )

        msg = IncomingMessage(
            external_user_id="alice",
            content=content,
            source_id="telegram",
        )

        with patch(
            "daemon.sources.registry.InstanceMapper",
            return_value=mock_mapper_instance,
        ), patch(
            "daemon.sources.mapper.get_registry",
            return_value=mock_agent_registry,
        ):
            await registry._handle_message("telegram", msg)

        manager.enqueue_message_job.assert_awaited_once()
        manager.set_injection.assert_not_called()


# ---------------------------------------------------------------------------
# Defensive probe-failure path → durable enqueue
# ---------------------------------------------------------------------------


class TestProbeFailureDurableFallback:
    """If the live-injection probe (``get_instance_info``) raises,
    the chat source MUST NOT silently drop the message — it MUST
    fall through to the durable ``enqueue_message_job`` path.
    """

    @pytest.mark.asyncio
    async def test_get_instance_info_keyerror_falls_through(self):
        """Transient ``KeyError`` from ``get_instance_info`` (e.g.,
        row deleted between mapping + status read) MUST route to
        durable enqueue — never a silent drop.
        """
        manager = _stub_manager(status="running", has_live_graph=True)
        manager.get_instance_info = MagicMock(side_effect=KeyError("miss"))
        registry = _build_registry_with_manager(manager)
        mock_mapper_instance = _stub_mapper()
        mock_agent_registry = MagicMock()
        mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
        mock_agent_registry.get = MagicMock(
            return_value=MagicMock(path="/default/agents")
        )

        msg = IncomingMessage(
            external_user_id="alice",
            content="hi",
            source_id="telegram",
        )

        with patch(
            "daemon.sources.registry.InstanceMapper",
            return_value=mock_mapper_instance,
        ), patch(
            "daemon.sources.mapper.get_registry",
            return_value=mock_agent_registry,
        ):
            # Must NOT raise — the probe is wrapped in try/except.
            await registry._handle_message("telegram", msg)

        manager.enqueue_message_job.assert_awaited_once()
        manager.set_injection.assert_not_called()


# ---------------------------------------------------------------------------
# Durable path still threads the full kwargs unchanged
# ---------------------------------------------------------------------------


class TestDurablePathByteIdentical:
    """When the durable enqueue path is taken, the ``enqueue_message_job``
    call signature is the SAME as before the injection branch was added
    — no kwargs added, removed, or reordered.
    """

    @pytest.mark.asyncio
    async def test_idle_path_enqueues_with_all_kwargs(self):
        """IDLE target: full ``enqueue_message_job`` kwargs thread
        unchanged (``instance_id``, ``message``, ``source``,
        ``priority``, ``images``, ``metadata``).
        """
        manager = _stub_manager(status="idle", has_live_graph=False)
        registry = _build_registry_with_manager(manager)
        mock_mapper_instance = _stub_mapper()
        mock_agent_registry = MagicMock()
        mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
        mock_agent_registry.get = MagicMock(
            return_value=MagicMock(path="/default/agents")
        )

        msg = IncomingMessage(
            external_user_id="alice",
            content="hello",
            source_id="telegram",
            metadata={"priority": 7, "message_id": "t-100"},
        )

        with patch(
            "daemon.sources.registry.InstanceMapper",
            return_value=mock_mapper_instance,
        ), patch(
            "daemon.sources.mapper.get_registry",
            return_value=mock_agent_registry,
        ):
            await registry._handle_message("telegram", msg, priority=7)

        manager.enqueue_message_job.assert_awaited_once()
        kwargs = manager.enqueue_message_job.call_args.kwargs
        assert kwargs["instance_id"] == "instance-123"
        assert kwargs["message"] == "hello"
        assert kwargs["source"] == "telegram:alice"
        assert kwargs["priority"] == 7
        assert kwargs["images"] is None
        assert kwargs["metadata"] == {"priority": 7, "message_id": "t-100"}
