"""Hook-level tests for the persistent-context SSE emission path
inside :meth:`InstanceMessagingService._process_message_with_tracking`.

The file covers the **persistent-context SSE emission** path —
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
tests focused on the persistent-context SSE emission.

Each test follows the same shape:

* build a manager mock with controllable
  ``shared_meta_kv_repo`` / instance metadata /
  ``shared_context_injected`` flag state
* patch the graph's ``astream`` to capture ``graph_input`` and
  immediately end iteration
* invoke ``_process_message_with_tracking`` and assert on the
  captured ``stream_message`` calls
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
    ``shared_meta_kv_repo.get_all_meta_kv_as_dict`` return an empty
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
    manager.shared_meta_kv_repo = repo
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
            registry.get_version = MagicMock(return_value=None)
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
            registry.get_version = MagicMock(return_value=None)
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


# ============================================================
# Regression tests — project_injected flag write path (W3, 2026-07-31)
# ============================================================


class TestProjectInjectedFlagWritePath:
    """W3 — pin the once-per-instance ``project_injected`` flag write path.

    The flag is stamped onto the instance metadata by
    :meth:`InstanceMessagingService._process_message_with_tracking`
    after a successful project match (parent-stamped
    ``project_id`` or keyword match). Once written, the flag is read
    fresh on every subsequent turn by
    :meth:`daemon.graph.ContextSlot._is_project_already_injected` to
    short-circuit the persistent-block rebuild.

    Earlier revisions dropped the assertion when cleaning up legacy
    tests. These two tests pin the contract end-to-end so a future
    refactor cannot silently break the once-per-instance gate.
    """

    async def test_successful_project_match_writes_project_injected_flag(self):
        """After a successful project match the flag is stamped onto metadata.

        Regression for W3: pin the contract that on a successful
        first-turn project injection, the instance metadata MUST
        carry ``project_injected=True`` so subsequent turns can
        short-circuit the persistent rebuild. The exact method,
        key, and value matter — the slot's ``_is_project_already_injected``
        checks for ``bool(metadata.get("project_injected"))`` via the
        captured ``instance_repository`` on every ``assemble()`` call.
        """
        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={},
            shared_context_injected=False,
            project_injected=False,  # first-turn state → flag will be written
        )

        # Wire the project_repository to return a successful match
        # for the existing project_id ("proj-1") — exercises the
        # ``if existing_project_id:`` branch (parent-stamped project_id).
        manager._project_repository.get = MagicMock(
            return_value=SimpleNamespace(
                project_id="proj-1",
                name="Matched Project",
            )
        )

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            registry.get_version = MagicMock(return_value=None)
            registry.get_resolved = MagicMock(
                return_value=SimpleNamespace(context_injection_mode="human_messages")
            )
            mock_get_registry.return_value = registry

            svc = _make_service(manager)
            manager.get_instance.return_value = graph

            with patch(
                "daemon.services.context_messages.assemble_context_messages",
                new=AsyncMock(return_value=([], [])),
            ):
                await svc._process_message_with_tracking(
                    instance_id="inst-1",
                    message="hello",
                    message_id="msg-1",
                    is_retry=False,
                    message_source="agent:leader",
                )

        # Verify the flag was written exactly once with the
        # documented contract: instance_id, "project_injected", True.
        flag_writes = [
            c for c in manager._instance_repository.set_metadata.call_args_list
            if len(c.args) >= 3 and c.args[1] == "project_injected"
        ]
        assert len(flag_writes) == 1, (
            f"expected exactly one set_metadata call writing 'project_injected', "
            f"got {len(flag_writes)}: {flag_writes!r}"
        )
        # The exact args the slot's _is_project_already_injected reads back:
        # instance_id, key, value.
        assert flag_writes[0].args[0] == "inst-1"
        assert flag_writes[0].args[1] == "project_injected"
        assert flag_writes[0].args[2] is True

    async def test_failed_project_match_does_not_write_project_injected_flag(self):
        """W3 — when the project match fails the flag is NOT written.

        Regression: a previous implementation accidentally wrote
        the flag even on a failed match. The flag must only be
        written when a project was actually matched so a subsequent
        retry can attempt matching again. Without this guard, a
        transient failure on turn 1 would permanently suppress
        the project context for the lifetime of the instance.
        """
        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={},
            shared_context_injected=False,
            project_injected=False,  # first-turn state → would write on success
        )

        # Wire project_repository.get to return None — the match failed.
        # This is the "project_id exists in metadata but the project was
        # deleted" case, or a DB lookup error swallowed upstream.
        manager._project_repository.get = MagicMock(return_value=None)

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            registry.get_version = MagicMock(return_value=None)
            registry.get_resolved = MagicMock(
                return_value=SimpleNamespace(context_injection_mode="human_messages")
            )
            mock_get_registry.return_value = registry

            svc = _make_service(manager)
            manager.get_instance.return_value = graph

            with patch(
                "daemon.services.context_messages.assemble_context_messages",
                new=AsyncMock(return_value=([], [])),
            ):
                await svc._process_message_with_tracking(
                    instance_id="inst-1",
                    message="hello",
                    message_id="msg-1",
                    is_retry=False,
                    message_source="agent:leader",
                )

        # The flag MUST NOT be written on a failed match.
        flag_writes = [
            c for c in manager._instance_repository.set_metadata.call_args_list
            if len(c.args) >= 3 and c.args[1] == "project_injected"
        ]
        assert len(flag_writes) == 0, (
            f"flag must NOT be written on a failed match — got: {flag_writes!r}"
        )
