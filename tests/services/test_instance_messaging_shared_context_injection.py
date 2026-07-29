"""Hook-level tests for the message-body injection of
``shared_context_metadata`` KV inside
:meth:`InstanceMessagingService._process_message_with_tracking`.

The system-prompt injection
(:func:`daemon.services.instance_lifecycle.append_shared_context_metadata`)
is already covered by ``test_shared_context_injection.py`` /
``test_shared_context_prompt_injection.py``. This file fills the
test gap for the **message-body** injection wired into the
leader→child delivery path: the new
``format_shared_context_for_message_body`` call + the
``shared_context_injected`` once-per-instance flag.

Also covers the **persistent-context SSE emission** path —
``user_message`` events for the persistent block (project / shared-
context / skills) injected on the first turn. Without those events,
the frontend never sees a user bubble for the persistent context and
the conversation appears to start at the actual user query, even
though the agent received the context.

Implementation strategy
-----------------------

``_process_message_with_tracking`` is a 600-line method. We don't
try to exercise all of it. Instead we wire every dependency to a
mock, then capture the ``graph_input`` dict the hook hands to
``graph.astream(...)`` and assert on the ``messages`` list's text
content.

The skill-injection path (which runs in the same block of
``_process_message_with_tracking``) is patched out by setting
``manager._skill_injection_service = None`` and using an agent
metadata object without ``skill_injection`` enabled — keeps the
tests focused on the shared-context injection.

Each test follows the same shape:

* build a manager mock with controllable
  ``shared_context_metadata_repo`` / instance metadata /
  ``shared_context_injected`` flag state
* patch the graph's ``astream`` to capture ``graph_input`` and
  immediately end iteration
* invoke ``_process_message_with_tracking`` and assert on the
  captured text content of the user ``HumanMessage``
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from daemon.services.instance_messaging import InstanceMessagingService


# ============================================================
# Helpers (mirror tests/services/test_instance_messaging_skill_injection.py)
# ============================================================


def _make_capturing_graph(captured: dict) -> MagicMock:
    """Build a LangGraph mock whose ``astream`` captures the
    ``graph_input`` it receives, then immediately ends iteration
    so the surrounding ``async for event in graph.astream(...)``
    in ``_process_message_with_tracking`` exits cleanly.

    Tests assert against ``captured["graph_input"]``.
    """

    async def fake_astream(*args, **kwargs):
        if args:
            captured["graph_input"] = args[0]
        elif "graph_input" in kwargs:
            captured["graph_input"] = kwargs["graph_input"]
        return
        yield  # pragma: no cover

    graph = MagicMock()
    graph.astream = fake_astream
    return graph


@asynccontextmanager
async def _null_semaphore():
    yield


def _make_manager(
    *,
    shared_context_kvs: dict | None = None,
    shared_context_injected: bool = False,
    parent_id: str | None = None,
    project_injected: bool = True,
    raise_on_get_kvs: Exception | None = None,
    agent_id: str = "developer",
) -> MagicMock:
    """Build a manager mock with controlled shared-context state.

    ``shared_context_kvs=None`` (default) makes the
    ``shared_context_metadata_repo.get_all_as_dict`` return an empty
    dict — the same as the production "no metadata for this
    context yet" path. Pass a populated dict to exercise the
    injection branch; pass ``raise_on_get_kvs`` to exercise the
    graceful-degradation branch.

    ``shared_context_injected=False`` (default) is the "first
    message" state; ``True`` skips the injection per the
    once-per-instance contract.
    """
    instance_meta = SimpleNamespace(
        instance_id="inst-1",
        agent_id=agent_id,
        parent_id=parent_id,
        instance_metadata={
            "project_id": "proj-1",
            "project_injected": project_injected,
            "shared_context_injected": shared_context_injected,
        },
    )

    manager = MagicMock()
    manager.config.limits.graph_recursion_limit = 50
    manager.config.compaction = MagicMock()
    manager.get_instance = AsyncMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=instance_meta)
    manager._instance_repository.set_metadata = MagicMock(return_value=None)
    manager._live_hub = MagicMock()
    manager._live_hub.stream_message = AsyncMock()
    manager._queue_repository = MagicMock()
    manager._graph_tasks = {}
    manager.source_dispatcher = None
    manager._llm_semaphore = _null_semaphore()

    # No skill injection service — keeps the test focused on the
    # shared-context injection. The hook's ``getattr(..., None)``
    # degrades to a no-op in this case.
    manager._skill_injection_service = None

    # Shared-context metadata repo: real interface, mocked return.
    repo = MagicMock()
    if raise_on_get_kvs is not None:
        repo.get_all_as_dict.side_effect = raise_on_get_kvs
    else:
        repo.get_all_as_dict.return_value = shared_context_kvs or {}
    # Tree-root resolution returns ``parent_id`` itself (root-instance
    # branch) — production uses ``get_tree_root_id`` only when
    # ``parent_id`` is provided. By default this mock returns
    # ``None`` which the formatter falls back from.
    manager._instance_repository.get_tree_root_id = MagicMock(
        return_value=None
    )
    manager.shared_context_metadata_repo = repo
    return manager


def _make_service(manager: MagicMock) -> InstanceMessagingService:
    """Build an :class:`InstanceMessagingService` around ``manager``,
    with the checkpoint/compaction helpers stubbed so the body of
    ``_process_message_with_tracking`` always takes the
    "first-attempt" branch (where ``graph_input`` is built from
    scratch) regardless of the ``is_retry`` flag.
    """
    svc = InstanceMessagingService(
        manager=manager,
        cancellation_service=MagicMock(is_shutting_down=False),
    )
    svc._has_checkpoint = AsyncMock(return_value=False)
    svc._maybe_compact_context = AsyncMock()
    return svc


def _captured_user_message_text(captured: dict) -> str:
    """Extract the user message text from a captured ``graph_input``.

    The skill-injection path (which we disable via
    ``_skill_injection_service=None``) is the only other path that
    prepends an extra ``HumanMessage`` before the user message.
    With it disabled, the captured graph_input is a single-element
    ``messages`` list — the user ``HumanMessage`` whose ``content``
    is the rendered leader→child request (including any injected
    blocks).
    """
    assert captured, "graph_input was never captured"
    graph_input = captured.get("graph_input")
    assert graph_input is not None, "graph_input was never captured"
    assert "messages" in graph_input
    assert len(graph_input["messages"]) >= 1
    user_msg = graph_input["messages"][-1]
    content = user_msg.content
    if isinstance(content, list):
        # Multimodal content — extract the first text block.
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block["text"]
        return ""
    return content


# ============================================================
# Hook tests — _process_message_with_tracking
# ============================================================


class TestMessageBodySharedContextInjection:
    """End-to-end exercises of the message-body injection gate.

    Each test invokes ``_process_message_with_tracking`` once and
    asserts on the captured ``graph_input`` text content plus the
    ``set_metadata`` calls.
    """

    async def test_injects_shared_context_block_into_message_body(self):
        """A populated KV set is prepended to the user message body.

        Pins the core Option-C contract: the leader's request
        arrives at the child with the ``# Shared Context`` /
        ``## Metadata KV`` block (and the ``<shared_context_metadata>``
        data fence) visibly prepended, NOT just sitting in the
        system prompt.

        Opts into ``legacy`` mode via the registry mock's
        ``context_injection_mode`` so the message-body prepend
        branch runs. In ``human_messages`` mode (the new default)
        the KV is rebuilt per-turn inside ``agent_node`` by
        :func:`daemon.services.context_messages.assemble_context_messages`
        — prepending here would double-inject. The legacy path
        keeps the message-body block as the only source of these
        metadata KV entries (the system-prompt appenders do not
        carry them in legacy mode).
        """
        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={"scope": "LARGE", "priority": 1},
        )

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            # ``get_resolved`` returns an AgentMeta stub in ``legacy``
            # mode — opts into the pre-restructure pipeline where the
            # message-body prepend is the only source of the KV
            # block. In ``human_messages`` mode (the new default) the
            # prepend branch is skipped and KV is rebuilt per-turn
            # inside ``agent_node``.
            registry.get_resolved = MagicMock(
                return_value=SimpleNamespace(context_injection_mode="legacy")
            )
            mock_get_registry.return_value = registry

            svc = _make_service(manager)
            manager.get_instance.return_value = graph

            await svc._process_message_with_tracking(
                instance_id="inst-1",
                message="Please implement feature X.",
                message_id="msg-1",
                is_retry=False,
                message_source="agent:leader",
            )

        # The message body MUST contain the shared-context block.
        user_text = _captured_user_message_text(captured)
        assert "# Shared Context" in user_text
        assert "## Metadata KV" in user_text
        assert "<shared_context_metadata>" in user_text
        assert "</shared_context_metadata>" in user_text
        # The KV payload appears inside the fence.
        assert '"scope"' in user_text
        assert '"LARGE"' in user_text
        assert '"priority"' in user_text
        # The leader's actual message is preserved.
        assert "Please implement feature X." in user_text

    async def test_block_is_prepended_before_leader_message(self):
        """The block precedes the leader's request — the documented layout.

        Verifies the ``[shared context] / --- / [project context] /
        --- / [leader's request]`` ordering required by the spec.
        """
        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={"k": "v"},
        )

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            # Opt into ``legacy`` mode (Phase 6 default flip means ``None``
            # now resolves to ``human_messages``, which skips the message-body
            # prepend — the legacy path keeps it as the only source of KV).
            registry.get_resolved = MagicMock(
                return_value=SimpleNamespace(context_injection_mode="legacy")
            )
            mock_get_registry.return_value = registry

            svc = _make_service(manager)
            manager.get_instance.return_value = graph

            await svc._process_message_with_tracking(
                instance_id="inst-1",
                message="leader request body",
                message_id="msg-1",
                is_retry=False,
                message_source="agent:leader",
            )

        user_text = _captured_user_message_text(captured)

        # Shared-context block comes BEFORE the leader's message.
        sc_pos = user_text.find("# Shared Context")
        msg_pos = user_text.find("leader request body")
        assert sc_pos != -1, "shared-context block not found"
        assert msg_pos != -1, "leader message not found"
        assert sc_pos < msg_pos, (
            "shared-context block must precede the leader's message"
        )

    async def test_shared_context_injected_flag_prevents_reinjection(self):
        """The ``shared_context_injected`` flag short-circuits subsequent messages.

        When the flag is already ``True`` in the instance metadata,
        the second message must NOT re-inject the shared-context
        block AND must NOT re-write the flag. Verifies the
        once-per-instance contract mirrors ``project_injected``.
        """
        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={"k": "v"},
            shared_context_injected=True,  # already injected
        )

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            registry.get_resolved = MagicMock(return_value=None)
            mock_get_registry.return_value = registry

            svc = _make_service(manager)
            manager.get_instance.return_value = graph

            await svc._process_message_with_tracking(
                instance_id="inst-1",
                message="second message",
                message_id="msg-2",
                is_retry=False,
                message_source="agent:leader",
            )

        user_text = _captured_user_message_text(captured)
        # No shared-context block — flag prevented re-injection.
        assert "# Shared Context" not in user_text
        # Leader's message is delivered verbatim.
        assert "second message" in user_text

        # The flag must NOT be re-written (otherwise we'd see a
        # redundant ``set_metadata(..., "shared_context_injected", True)``
        # call). Filter to just the shared-context flag writes.
        sc_flag_writes = [
            call for call in manager._instance_repository.set_metadata.call_args_list
            if call.args[1] == "shared_context_injected"
        ]
        assert sc_flag_writes == [], (
            "shared_context_injected flag must not be re-written "
            f"when already True; saw {sc_flag_writes!r}"
        )

    async def test_sets_shared_context_injected_flag_on_first_message(self):
        """First message flips the ``shared_context_injected`` flag to ``True``.

        Pins the persistence side of the once-per-instance guard.
        """
        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={"k": "v"},
            shared_context_injected=False,
        )

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            # Opt into ``legacy`` mode (Phase 6 default flip means ``None``
            # now resolves to ``human_messages``, which skips the message-body
            # prepend — the legacy path keeps it as the only source of KV).
            registry.get_resolved = MagicMock(
                return_value=SimpleNamespace(context_injection_mode="legacy")
            )
            mock_get_registry.return_value = registry

            svc = _make_service(manager)
            manager.get_instance.return_value = graph

            await svc._process_message_with_tracking(
                instance_id="inst-1",
                message="hello",
                message_id="msg-1",
                is_retry=False,
                message_source="agent:leader",
            )

        # Find the set_metadata call for shared_context_injected.
        sc_flag_writes = [
            call for call in manager._instance_repository.set_metadata.call_args_list
            if call.args[1] == "shared_context_injected"
        ]
        assert len(sc_flag_writes) == 1
        # ``args[0]`` is instance_id, ``args[1]`` is the key,
        # ``args[2]`` is the value.
        assert sc_flag_writes[0].args[0] == "inst-1"
        assert sc_flag_writes[0].args[2] is True

    async def test_empty_metadata_does_not_set_flag(self):
        """Empty KV set → no block injected, AND flag does NOT flip.

        Mirrors the ``project_injected`` behavior: only flip the
        once-per-instance flag when injection actually succeeded.
        Without this, metadata written by the leader BETWEEN
        message 1 (empty) and message 2 (populated) would be
        permanently invisible in the message body — the
        very gap Option C was meant to close.
        """
        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={},  # empty
            shared_context_injected=False,
        )

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            registry.get_resolved = MagicMock(return_value=None)
            mock_get_registry.return_value = registry

            svc = _make_service(manager)
            manager.get_instance.return_value = graph

            await svc._process_message_with_tracking(
                instance_id="inst-1",
                message="hello",
                message_id="msg-1",
                is_retry=False,
                message_source="agent:leader",
            )

        user_text = _captured_user_message_text(captured)
        # No shared-context block — KV was empty.
        assert "# Shared Context" not in user_text
        # Leader's message still delivered.
        assert "hello" in user_text

        # Flag MUST NOT flip (so the next message retries and can
        # pick up late-arriving metadata).
        sc_flag_writes = [
            call for call in manager._instance_repository.set_metadata.call_args_list
            if call.args[1] == "shared_context_injected"
        ]
        assert sc_flag_writes == [], (
            "shared_context_injected flag must not flip on empty KV — "
            "the next message would otherwise silently skip late-arriving metadata"
        )

    async def test_late_arriving_metadata_injected_on_second_message(self):
        """Metadata written BETWEEN messages gets injected on the next one.

        Pins the design intent of Option C: the message-body injection
        must reflect the **latest** KV at delivery time, not the
        stale snapshot from message 1. With the no-flip-on-empty
        flag behavior above, message 1 (empty) does not poison the
        flag, so message 2 (populated) still sees the block.
        """
        # --- Message 1: empty KV, flag stays False ---
        captured1: dict = {}
        graph1 = _make_capturing_graph(captured1)
        manager1 = _make_manager(
            shared_context_kvs={},
            shared_context_injected=False,
        )

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            # Opt into ``legacy`` mode (Phase 6 default flip means ``None``
            # now resolves to ``human_messages``, which skips the message-body
            # prepend — the legacy path keeps it as the only source of KV).
            registry.get_resolved = MagicMock(
                return_value=SimpleNamespace(context_injection_mode="legacy")
            )
            mock_get_registry.return_value = registry

            svc1 = _make_service(manager1)
            manager1.get_instance.return_value = graph1

            await svc1._process_message_with_tracking(
                instance_id="inst-1",
                message="first message",
                message_id="msg-1",
                is_retry=False,
                message_source="agent:leader",
            )

        # Flag must NOT have flipped.
        sc_flag_writes = [
            call for call in manager1._instance_repository.set_metadata.call_args_list
            if call.args[1] == "shared_context_injected"
        ]
        assert sc_flag_writes == []
        # Message 1 carries no block.
        assert "# Shared Context" not in _captured_user_message_text(captured1)

        # --- Message 2: leader has since written metadata via the tool ---
        captured2: dict = {}
        graph2 = _make_capturing_graph(captured2)
        # Fresh manager mock (each invocation is a separate call to
        # ``_process_message_with_tracking``). The KV snapshot now
        # reflects the late write; the flag is still False because
        # message 1 didn't flip it.
        manager2 = _make_manager(
            shared_context_kvs={"late_meta": "value"},
            shared_context_injected=False,
        )

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            # Opt into ``legacy`` mode (Phase 6 default flip means ``None``
            # now resolves to ``human_messages``, which skips the message-body
            # prepend — the legacy path keeps it as the only source of KV).
            registry.get_resolved = MagicMock(
                return_value=SimpleNamespace(context_injection_mode="legacy")
            )
            mock_get_registry.return_value = registry

            svc2 = _make_service(manager2)
            manager2.get_instance.return_value = graph2

            await svc2._process_message_with_tracking(
                instance_id="inst-1",
                message="second message",
                message_id="msg-2",
                is_retry=False,
                message_source="agent:leader",
            )

        # Message 2 carries the late metadata.
        msg2_text = _captured_user_message_text(captured2)
        assert "# Shared Context" in msg2_text
        assert '"late_meta"' in msg2_text

    async def test_repo_failure_does_not_break_message_delivery(self):
        """A repo exception degrades to "no block" — the message still flows.

        The graceful-degradation contract: a transient repo failure
        on the shared-context lookup must NOT abort message
        processing. The leader's request reaches the child with
        no shared-context block.

        Flag-flip on exception is NO-FLIP — mirrors ``project_injected``:
        an exception today doesn't mean the next message will hit the
        same error, so retrying the lookup preserves Option C's goal
        of reflecting the latest metadata at delivery time. The
        flag-flip behavior matches the empty-KV case (no block →
        no flip).
        """
        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            raise_on_get_kvs=RuntimeError("simulated DB failure"),
        )

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            registry.get_resolved = MagicMock(return_value=None)
            mock_get_registry.return_value = registry

            svc = _make_service(manager)
            manager.get_instance.return_value = graph

            await svc._process_message_with_tracking(
                instance_id="inst-1",
                message="hello",
                message_id="msg-1",
                is_retry=False,
                message_source="agent:leader",
            )

        user_text = _captured_user_message_text(captured)
        # No block — graceful degradation succeeded.
        assert "# Shared Context" not in user_text
        # Leader's message still delivered.
        assert "hello" in user_text
        # Flag MUST NOT flip on exception (mirrors the empty-KV case).
        sc_flag_writes = [
            call for call in manager._instance_repository.set_metadata.call_args_list
            if call.args[1] == "shared_context_injected"
        ]
        assert sc_flag_writes == [], (
            "shared_context_injected flag must not flip on repo exception"
        )

    async def test_completion_report_skips_injection(self):
        """Internal completion reports (``internal_report:*``) skip injection.

        Mirrors the project-context gate: completion reports are
        internal pings, not user-facing requests, so they must NOT
        receive the shared-context block.
        """
        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={"k": "v"},
        )

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            registry.get_resolved = MagicMock(return_value=None)
            mock_get_registry.return_value = registry

            svc = _make_service(manager)
            manager.get_instance.return_value = graph

            await svc._process_message_with_tracking(
                instance_id="inst-1",
                message="completion report body",
                message_id="msg-1",
                is_retry=False,
                message_source="internal_report:child-finished",
            )

        user_text = _captured_user_message_text(captured)
        assert "# Shared Context" not in user_text
        assert "completion report body" in user_text

        # Flag must NOT flip on a completion report — the completion
        # report is the "first message" for routing purposes but the
        # actual user-facing first message comes later. Leaving the
        # flag unset means the real first message gets the block.
        sc_flag_writes = [
            call for call in manager._instance_repository.set_metadata.call_args_list
            if call.args[1] == "shared_context_injected"
        ]
        assert sc_flag_writes == [], (
            "completion reports must not flip the shared_context_injected flag"
        )

    async def test_internal_error_report_skips_injection(self):
        """Internal error reports (``internal_error_report:*``) skip injection.

        Mirrors ``test_completion_report_skips_injection`` for the second
        of the three ``is_completion_report`` prefixes in
        ``_process_message_with_tracking``. Error reports are internal
        pings, so the shared-context block must NOT be prepended AND
        the ``shared_context_injected`` flag must NOT flip — otherwise
        the real first user message would silently miss the metadata.
        """
        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={"k": "v"},
        )

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            registry.get_resolved = MagicMock(return_value=None)
            mock_get_registry.return_value = registry

            svc = _make_service(manager)
            manager.get_instance.return_value = graph

            await svc._process_message_with_tracking(
                instance_id="inst-1",
                message="error report body",
                message_id="msg-1",
                is_retry=False,
                message_source="internal_error_report:some-error",
            )

        user_text = _captured_user_message_text(captured)
        assert "# Shared Context" not in user_text
        assert "error report body" in user_text

        sc_flag_writes = [
            call for call in manager._instance_repository.set_metadata.call_args_list
            if call.args[1] == "shared_context_injected"
        ]
        assert sc_flag_writes == [], (
            "internal_error_report must not flip the shared_context_injected flag"
        )

    async def test_internal_agent_job_event_skips_injection(self):
        """Internal agent job events (``internal_agent:job_event:*``) skip injection.

        Mirrors ``test_completion_report_skips_injection`` for the third
        of the three ``is_completion_report`` prefixes in
        ``_process_message_with_tracking``. Job events are internal pings
        from the scheduler, not user-facing requests, so the shared-
        context block must NOT be prepended AND the
        ``shared_context_injected`` flag must NOT flip — leaving the
        flag unset means the real first user message still gets the
        block.
        """
        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={"k": "v"},
        )

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            registry.get_resolved = MagicMock(return_value=None)
            mock_get_registry.return_value = registry

            svc = _make_service(manager)
            manager.get_instance.return_value = graph

            await svc._process_message_with_tracking(
                instance_id="inst-1",
                message="job event body",
                message_id="msg-1",
                is_retry=False,
                message_source="internal_agent:job_event:some-event",
            )

        user_text = _captured_user_message_text(captured)
        assert "# Shared Context" not in user_text
        assert "job event body" in user_text

        sc_flag_writes = [
            call for call in manager._instance_repository.set_metadata.call_args_list
            if call.args[1] == "shared_context_injected"
        ]
        assert sc_flag_writes == [], (
            "internal_agent:job_event must not flip the shared_context_injected flag"
        )

    async def test_32k_payload_does_not_set_flag_at_hook_level(self):
        """Shared-context metadata exceeding 32k cap must not inject AND must not flip the flag.

        Pins the no-flip-on-cap contract from the production code path: when
        ``_format_shared_context_kv_block`` returns ``None`` (over 32k cap),
        ``format_shared_context_for_message_body`` returns ``""``, so the
        hook's ``if sc_block:`` block is skipped and the flag stays unset.
        Without this, a one-time over-cap payload would silently poison the
        flag and prevent all future messages from receiving the metadata
        even after it shrinks below the cap.
        """
        captured: dict = {}
        graph = _make_capturing_graph(captured)
        # 35k chars — well over the 32k cap after ``json.dumps`` with
        # ``ensure_ascii=True``. Single key keeps the test deterministic.
        large_payload = {"huge": "x" * 35_000}
        manager = _make_manager(
            shared_context_kvs=large_payload,
            shared_context_injected=False,  # first-message state
        )

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            registry.get_resolved = MagicMock(return_value=None)
            mock_get_registry.return_value = registry

            svc = _make_service(manager)
            manager.get_instance.return_value = graph

            await svc._process_message_with_tracking(
                instance_id="inst-1",
                message="leader request body",
                message_id="msg-1",
                is_retry=False,
                message_source="agent:leader",
            )

        # Tightened: prove we actually reached the body.
        assert captured.get("graph_input") is not None, (
            "test must reach the body — if not, the assertions below are silent"
        )
        user_text = _captured_user_message_text(captured)
        # No block injected.
        assert "# Shared Context" not in user_text
        # Leader's request still delivered.
        assert "leader request body" in user_text

        # Flag MUST NOT flip on over-cap (mirrors no-flip-on-empty /
        # no-flip-on-exception). Otherwise a transient oversized payload
        # would silently block all future metadata injection.
        sc_flag_writes = [
            call for call in manager._instance_repository.set_metadata.call_args_list
            if call.args[1] == "shared_context_injected"
        ]
        assert sc_flag_writes == [], (
            "shared_context_injected flag must not flip on 32k cap — "
            "a transient oversized payload would otherwise block future metadata"
        )

    async def test_project_injected_shared_context_failed_retry(self):
        """When project injection succeeds but shared-context fails, the two paths are independent.

        Pins the independence contract: project context proceeds to inject
        + flip its flag; shared-context degrades to no-op (no block, no
        flag flip). The next message will re-attempt the shared-context
        lookup, so a transient failure doesn't permanently lock out
        metadata injection.
        """
        captured: dict = {}
        graph = _make_capturing_graph(captured)
        # Shared-context side FAILS — repo raises. Project side SUCCEEDS —
        # wires below.
        manager = _make_manager(
            raise_on_get_kvs=RuntimeError("simulated shared-context failure"),
            shared_context_injected=False,
            project_injected=False,
        )

        # Wire project-context injection to succeed.
        matched_project = MagicMock()
        matched_project.name = "test-project"
        matched_project.project_id = "proj-matched"
        manager._project_repository = MagicMock()
        manager._project_repository.match_by_keywords = MagicMock(
            return_value=matched_project
        )

        with patch(
            "daemon.services.instance_messaging.extract_project_keywords",
            return_value=["test", "project"],
            create=True,
        ):
            fake_project_context = "## Related Project\ntest-project\n"

            def _fake_format(*_args, **_kwargs):
                return fake_project_context

            with patch(
                "daemon.manager.format_project_context",
                _fake_format,
                create=True,
            ):
                with patch("daemon.registry.get_registry") as mock_get_registry:
                    registry = MagicMock()
                    # Opt into ``legacy`` mode (Phase 6 default flip means ``None``
                    # now resolves to ``human_messages``, which skips the message-body
                    # prepend — the legacy path keeps it as the only source of KV).
                    registry.get_resolved = MagicMock(
                        return_value=SimpleNamespace(context_injection_mode="legacy")
                    )
                    mock_get_registry.return_value = registry

                    svc = _make_service(manager)
                    manager.get_instance.return_value = graph

                    await svc._process_message_with_tracking(
                        instance_id="inst-1",
                        message="leader request body",
                        message_id="msg-1",
                        is_retry=False,
                        message_source="agent:leader",
                    )

        user_text = _captured_user_message_text(captured)
        # Project block IS injected (project side succeeded).
        assert "## Related Project" in user_text, (
            f"project block missing; rendered was: {user_text!r}"
        )
        # Shared-context block is NOT injected (shared-context side failed).
        assert "# Shared Context" not in user_text
        # Leader's request still delivered.
        assert "leader request body" in user_text

        # project_injected flag DID flip.
        project_flag_writes = [
            call for call in manager._instance_repository.set_metadata.call_args_list
            if call.args[1] == "project_injected"
        ]
        assert len(project_flag_writes) == 1, (
            f"project_injected flag must flip exactly once when project side succeeds; saw {project_flag_writes!r}"
        )
        assert project_flag_writes[0].args[2] is True

        # shared_context_injected flag did NOT flip (shared-context side failed).
        sc_flag_writes = [
            call for call in manager._instance_repository.set_metadata.call_args_list
            if call.args[1] == "shared_context_injected"
        ]
        assert sc_flag_writes == [], (
            f"shared_context_injected flag must not flip when shared-context side fails; saw {sc_flag_writes!r}"
        )

    async def test_retry_skips_injection(self):
        """``is_retry=True`` short-circuits the injection gate.

        On retry, LangGraph's ``add_messages`` reducer re-attaches
        the original user message from the checkpoint — injecting
        again would duplicate the shared-context block. Mirrors
        the skill-injection gate at the same hook level.
        """
        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={"k": "v"},
        )

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            registry.get_resolved = MagicMock(return_value=None)
            mock_get_registry.return_value = registry

            svc = _make_service(manager)
            manager.get_instance.return_value = graph

            await svc._process_message_with_tracking(
                instance_id="inst-1",
                message="retry attempt",
                message_id="msg-1",
                is_retry=True,
                message_source="agent:leader",
            )

        # Tightened: the retry path MUST capture graph_input (proves the
        # test is actually exercising the body), and the captured user
        # message MUST NOT carry the shared-context block.
        assert captured.get("graph_input") is not None, (
            "retry path must capture graph_input — if this fails, the "
            "test is silently skipping the assertion below"
        )
        user_text = _captured_user_message_text(captured)
        assert "# Shared Context" not in user_text, (
            "is_retry=True must suppress shared-context injection — the "
            "checkpoint already carries the original user message"
        )

    async def test_dual_injection_blocks_share_correct_order(self):
        """When BOTH project AND shared-context inject, order is ``[sc][project][msg]``.

        Pins the positional contract: shared-context runs LAST in the
        hook, so its ``prepend`` lands it at the LEFT in the final
        rendered message — producing the documented
        ``[shared context] / --- / [project context] / --- / [leader's request]``
        layout from the plan doc.

        Every other test sets ``project_injected=True`` so this case
        is otherwise untested. This is the one case where order
        matters, so the assertion is non-negotiable.
        """
        captured: dict = {}
        graph = _make_capturing_graph(captured)
        # Force BOTH blocks to inject: shared_context_kvs populated,
        # shared_context_injected=False; project_injected=False.
        manager = _make_manager(
            shared_context_kvs={"k": "v"},
            shared_context_injected=False,
            project_injected=False,
        )

        # Project-context injection requires the keyword extraction /
        # project-repo paths to succeed. Mock them so the project
        # block actually prepends (the default ``MagicMock`` would
        # produce a ``MagicMock`` content and break the assertion).
        from daemon.repositories.project.models import Project as _Project  # noqa: F401

        manager._project_repository = MagicMock()
        # No stored project_id → the keyword-match branch runs.
        # Provide a non-empty project so the injection prepends a
        # deterministic string we can search for.
        matched_project = MagicMock()
        matched_project.name = "test-project"
        matched_project.project_id = "proj-matched"
        manager._project_repository.match_by_keywords = MagicMock(
            return_value=matched_project
        )
        # ``extract_project_keywords`` is a pure function imported
        # lazily — patch it to return non-empty so the project-context
        # branch fires.
        with patch(
            "daemon.services.instance_messaging.extract_project_keywords",
            return_value=["test", "project"],
            create=True,
        ):
            # ``format_project_context`` is also lazy-imported. Patch
            # both the import location AND the function call site to
            # return a recognizable string.
            fake_project_context = "## Related Project\ntest-project\n"

            def _fake_format(*_args, **_kwargs):
                return fake_project_context

            with patch(
                "daemon.manager.format_project_context",
                _fake_format,
                create=True,
            ):
                with patch("daemon.registry.get_registry") as mock_get_registry:
                    registry = MagicMock()
                    # Opt into ``legacy`` mode (Phase 6 default flip means ``None``
                    # now resolves to ``human_messages``, which skips the message-body
                    # prepend — the legacy path keeps it as the only source of KV).
                    registry.get_resolved = MagicMock(
                        return_value=SimpleNamespace(context_injection_mode="legacy")
                    )
                    mock_get_registry.return_value = registry

                    svc = _make_service(manager)
                    manager.get_instance.return_value = graph

                    await svc._process_message_with_tracking(
                        instance_id="inst-1",
                        message="leader request body",
                        message_id="msg-1",
                        is_retry=False,
                        message_source="agent:leader",
                    )

        user_text = _captured_user_message_text(captured)

        # Both blocks must appear in the rendered message.
        assert "# Shared Context" in user_text
        assert "## Related Project" in user_text
        assert "leader request body" in user_text

        # Order: shared-context block BEFORE project-context block,
        # BOTH before the leader's request.
        sc_pos = user_text.find("# Shared Context")
        project_pos = user_text.find("## Related Project")
        msg_pos = user_text.find("leader request body")
        assert sc_pos != -1 and project_pos != -1 and msg_pos != -1, (
            f"missing block(s): sc={sc_pos}, project={project_pos}, "
            f"msg={msg_pos}; rendered was: {user_text!r}"
        )
        assert sc_pos < project_pos, (
            "shared-context block must precede project-context block — "
            f"got sc@{sc_pos} project@{project_pos}"
        )
        assert project_pos < msg_pos, (
            "project-context block must precede the leader's request — "
            f"got project@{project_pos} msg@{msg_pos}"
        )

    async def test_child_instance_uses_tree_root_for_context_key(self):
        """A child instance's injection queries the root's KV via ``parent_id``.

        Pin the context-key-resolution contract at the hook level
        (separate from the unit-test contract in
        ``test_shared_context_message_body_injection.py``).
        When ``parent_id`` is set on the instance, the formatter
        must call ``get_tree_root_id(parent_id)`` to resolve the
        context key. The mock returns ``None`` so the formatter
        falls back to ``parent_id`` itself — proving the
        fallback path is wired through the hook.
        """
        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={"k": "v"},
            parent_id="parent-1",
        )

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            # Opt into ``legacy`` mode (Phase 6 default flip means ``None``
            # now resolves to ``human_messages``, which skips the message-body
            # prepend — the legacy path keeps it as the only source of KV).
            registry.get_resolved = MagicMock(
                return_value=SimpleNamespace(context_injection_mode="legacy")
            )
            mock_get_registry.return_value = registry

            svc = _make_service(manager)
            manager.get_instance.return_value = graph

            await svc._process_message_with_tracking(
                instance_id="child-1",
                message="hello",
                message_id="msg-1",
                is_retry=False,
                message_source="agent:leader",
            )

        # The repository was queried — proves the wiring ran.
        manager.shared_context_metadata_repo.get_all_as_dict.assert_called()

        # ``get_tree_root_id`` was called with the child's parent_id,
        # then the formatter fell back to ``parent_id`` itself when
        # the mock returned None.
        manager._instance_repository.get_tree_root_id.assert_called_with("parent-1")


# ============================================================
# Regression tests — persistent context SSE emission (CapFix 2026-07-29)
# ============================================================


class TestPersistentContextSseEmission:
    """Pins the ``user_message`` SSE emission for the persistent context block.

    CapFix 2026-07-29 added SSE emission for the persistent context
    HumanMessages (project + shared-context + skills) so the
    frontend sees a user bubble for each one on the first turn.
    Without these emissions the conversation appears to start at the
    actual user query even though ``agent_node`` receives the
    persistent block — the frontend is told a different story than
    the agent gets.

    These tests exercise the emission path through ``_process_message_with_tracking``
    with ``assemble_context_messages`` patched to return a known
    list of context messages, then inspect the ``stream_message``
    calls recorded by the manager mock.
    """

    async def test_persistent_context_emits_user_message_per_context_msg(self):
        """Each persistent context HumanMessage emits a ``user_message`` SSE event.

        Regression for the CapFix 2026-07-29 SSE emissions path: when
        ``assemble_context_messages`` returns N persistent context
        messages, the SSE layer MUST emit N+1 ``stream_message`` calls
        — N for the context (each carrying ``event_type="user_message"``,
        ``checkpoint_id="user"``, and the serialized context payload),
        plus 1 for the actual user message that follows.

        Mirrors the production ``user_message`` SSE envelope documented
        in ``daemon/graph.py:1867-1886`` (``checkpoint_id="user"`` for
        proper UI bubble rendering).
        """
        from langchain_core.messages import HumanMessage as _HM  # local for grouping

        # Two persistent context messages — the orchestrator's output.
        ctx_1 = _HM(
            content="[SYSTEM CONTEXT: Project]\nProject X is a test fixture.",
            id="ctx-pm-1",
        )
        ctx_2 = _HM(
            content="[SYSTEM CONTEXT: Shared Context]\nscope=LARGE priority=1",
            id="ctx-pm-2",
        )

        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={},  # legacy branch disabled; we drive context directly
            shared_context_injected=False,
            project_injected=False,  # first-turn state → orchestrator builds persistent block
        )

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            # Opt into ``human_messages`` mode so the persistent block
            # is built via ``assemble_context_messages`` rather than
            # landed in the legacy system-prompt appenders.
            registry.get_resolved = MagicMock(
                return_value=SimpleNamespace(context_injection_mode="human_messages")
            )
            mock_get_registry.return_value = registry

            svc = _make_service(manager)
            manager.get_instance.return_value = graph

            with patch(
                "daemon.services.context_messages.assemble_context_messages",
                new=AsyncMock(return_value=([ctx_1, ctx_2], [])),
            ):
                await svc._process_message_with_tracking(
                    instance_id="inst-1",
                    message="Please implement feature X.",
                    message_id="msg-1",
                    is_retry=False,
                    message_source="agent:leader",
                )

        # Tightened: the body MUST have run and captured the graph_input.
        assert captured.get("graph_input") is not None, (
            "test must reach the body — if not, the assertions below are silent"
        )

        # Inspect every stream_message call on the live_hub mock.
        # We expect N context messages + 1 user message = N+1 user_message
        # calls total.
        stream_calls = manager._live_hub.stream_message.call_args_list
        user_message_calls = [
            c for c in stream_calls
            if c.kwargs.get("event_type") == "user_message"
        ]
        # Both context emissions AND the regular user message use
        # event_type="user_message" + checkpoint_id="user", so the
        # count is N+1.
        assert len(user_message_calls) == 2 + 1, (
            f"expected one user_message SSE call per persistent context msg "
            f"plus the user message itself (3 total), got {len(user_message_calls)} "
            f"calls. All stream_message calls: {stream_calls!r}"
        )

        # Every user_message call MUST carry checkpoint_id="user"
        # (the documented SSE envelope from agent_node).
        for call in user_message_calls:
            assert call.kwargs.get("checkpoint_id") == "user", (
                "user_message SSE events must carry checkpoint_id='user' — "
                f"got {call.kwargs!r}"
            )

        # Identify the two context-message calls and verify their content
        # is the injected context payload, NOT the user's actual message.
        # ``serialize_message`` returns a dict with ``content``; extract
        # the human-readable text for the substring check.
        def _call_message_text(call) -> str:
            message = call.kwargs.get("message") or {}
            text = message.get("content", "")
            if isinstance(text, list):
                # Multimodal content — pull the first text block.
                for block in text:
                    if isinstance(block, dict) and block.get("type") == "text":
                        return block.get("text", "")
                return ""
            return str(text)

        context_call_texts = [
            _call_message_text(c) for c in user_message_calls[:2]
        ]
        # Both pre-emit context messages carry the SYSTEM CONTEXT prefix
        # (mirrors the production format produced by
        # ``_make_context_message`` in ``daemon.services.context_messages``).
        assert any("SYSTEM CONTEXT: Project" in t for t in context_call_texts), (
            f"expected one user_message call to carry the project context "
            f"payload — got context_call_texts={context_call_texts!r}"
        )
        assert any("SYSTEM CONTEXT: Shared Context" in t for t in context_call_texts), (
            f"expected one user_message call to carry the shared-context "
            f"payload — got context_call_texts={context_call_texts!r}"
        )

        # The final user_message call carries the actual user query.
        # Note: user_msg may have been wrapped via _build_message_content,
        # so we assert on substring rather than exact match.
        user_call_text = _call_message_text(user_message_calls[2])
        assert "Please implement feature X." in user_call_text, (
            f"the final user_message call must carry the user's actual "
            f"query — got {user_call_text!r}"
        )

    async def test_persistent_context_dedup_suppresses_repeat_emission(self):
        """Repeated invocations with the same context hash must not re-emit.

        Pin the dedup contract from
        ``InstanceMessagingService._process_message_with_tracking``
        around the SSE pre-emit loop: the manager-level
        ``_emitted_message_content`` dict is checked before each
        ``stream_message`` call so a retry (or any second invocation
        with the same persistent block) does not duplicate the
        frontend bubbles. Without this, retried messages would each
        re-emit N context bubbles, causing the frontend to display
        the same context repeatedly.
        """
        ctx_1 = HumanMessage(
            content="[SYSTEM CONTEXT: Skills]\nSkill block.",
            id="ctx-sk-1",
        )

        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={},
            shared_context_injected=False,
            project_injected=False,
        )

        # Pre-seed the manager's dedup dict with the hash for ctx_1, so the
        # second invocation short-circuits before calling stream_message
        # for the context. The user message itself is still emitted.
        from daemon.services.instance_messaging import (
            _compute_message_content_hash,
            serialize_message,
        )
        ctx_serialized = serialize_message(ctx_1)
        ctx_serialized["instance_id"] = "inst-1"
        ctx_hash = _compute_message_content_hash(ctx_serialized)
        manager._emitted_message_content = {
            f"inst-1:context:{ctx_1.id}": ctx_hash,
        }

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            registry.get_resolved = MagicMock(
                return_value=SimpleNamespace(context_injection_mode="human_messages")
            )
            mock_get_registry.return_value = registry

            svc = _make_service(manager)
            manager.get_instance.return_value = graph

            with patch(
                "daemon.services.context_messages.assemble_context_messages",
                new=AsyncMock(return_value=([ctx_1], [])),
            ):
                await svc._process_message_with_tracking(
                    instance_id="inst-1",
                    message="redo after retry",
                    message_id="msg-1",
                    is_retry=False,
                    message_source="agent:leader",
                )

        stream_calls = manager._live_hub.stream_message.call_args_list
        user_message_calls = [
            c for c in stream_calls
            if c.kwargs.get("event_type") == "user_message"
        ]
        # Only the user's own message emits — the context dedup short-circuits.
        assert len(user_message_calls) == 1, (
            "pre-seeded context dedup must suppress the context re-emit "
            f"— got {len(user_message_calls)} user_message calls: "
            f"{user_message_calls!r}"
        )
        # The single emission carries the user query (NOT the context block).
        message = user_message_calls[0].kwargs.get("message") or {}
        text = message.get("content", "")
        if isinstance(text, list):
            for block in text:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    break
        assert "redo after retry" in str(text)
        assert "SYSTEM CONTEXT" not in str(text)
