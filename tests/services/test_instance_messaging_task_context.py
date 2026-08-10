"""Service-level tests for the ``task_context`` HumanMessage injection
path inside :meth:`InstanceMessagingService._process_message_with_tracking`.

The new ``context`` parameter on ``send_message`` formats a parent-provided
dict into a ``[SYSTEM CONTEXT: Task Context]`` markdown block. At the
service level, that text arrives as the ``task_context`` kwarg of
``_process_message_with_tracking`` and is injected as a
:class:`langchain_core.messages.HumanMessage` AFTER the stable context
blocks (project / shared-context / skills) but before the task message
(so stable blocks stay at the top for prompt cache efficiency) on the
first attempt.
The retry path is intentionally excluded — the message is checkpointed
on turn 1, so re-emitting it on a retry would double-inject.

Implementation strategy
-----------------------

``_process_message_with_tracking`` is a 600+ line method. We follow the
capturing-graph pattern from
:mod:`tests.services.test_instance_messaging_shared_context_injection`:

* build a manager mock with controllable ``shared_meta_kv_repo``
  / instance metadata / ``shared_context_injected`` flag state
* patch the graph's ``astream`` to capture ``graph_input`` and
  immediately end iteration
* invoke ``_process_message_with_tracking`` with various combinations
  of ``task_context`` and ``is_retry``
* assert on ``captured["graph_input"]["messages"]`` — the persistent
  block sits BEFORE the user message in the list, and the
  ``task_context`` HumanMessage is appended at the END of the
  persistent block (after the stable context blocks)

Each test class exercises one slice of the contract:

* :class:`TestTaskContextHumanMessageInjection` — basic shape
  (``task_context`` set, ``is_retry=False``), position (appended after
  stable context blocks), guards (``None`` / empty / retry).
* :class:`TestTaskContextAdditionalKwargs` — ``additional_kwargs`` carry
  ``injected_message=True`` and the canonical ``context_kind`` value.
* :class:`TestTaskContextWithOtherContextBlocks` — interaction with the
  shared-context / skill blocks emitted by
  :func:`daemon.services.context_messages.assemble_context_messages`.
* :class:`TestTaskContextInPersistenceContextKinds` — the read-path
  guard in :mod:`daemon.persistence` recognises the new
  ``context_kind`` so ``GET /messages`` doesn't rebuild a synthetic
  context block.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from daemon.services.instance_messaging import InstanceMessagingService


# ============================================================
# Helpers (mirror tests/services/test_instance_messaging_shared_context_injection.py)
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
    agent_id: str = "worker",
) -> MagicMock:
    """Build a manager mock with controlled state for the
    ``task_context`` injection path.

    Skill-injection is disabled via ``_skill_injection_service = None``
    and an agent metadata object without ``skill_injection`` enabled —
    the persistent block ends up holding only what
    :func:`assemble_context_messages` returns (driven by the tests
    through ``patch``). With no skill-injection side effects the
    ``task_context`` HumanMessage is appended at the END of
    ``persistent_context_msgs`` deterministically (after the stable
    blocks returned by ``assemble_context_messages``).
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
    manager._instance_repository.get_tree_root_id = MagicMock(
        return_value=None
    )
    manager._live_hub = MagicMock()
    manager._live_hub.stream_message = AsyncMock()
    manager._queue_repository = MagicMock()
    manager._graph_tasks = {}
    manager.source_dispatcher = None
    manager._llm_semaphore = _null_semaphore()

    # No skill injection service — keeps the test focused on the
    # task_context injection. The hook's ``getattr(..., None)``
    # degrades to a no-op in this case.
    manager._skill_injection_service = None

    # Shared-context metadata repo: real interface, mocked return.
    repo = MagicMock()
    repo.get_all_as_dict.return_value = shared_context_kvs or {}
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


def _find_task_context_msg(captured: dict) -> HumanMessage | None:
    """Pull the task_context ``HumanMessage`` (if any) out of the
    captured ``graph_input``.

    The captured ``messages`` list is ``[persistent_1, ..., persistent_n, user_message]``,
    so we walk it looking for a message whose ``additional_kwargs``
    carries ``context_kind == "task_context"``. The function returns
    the first such message, or ``None`` if the block was not injected.
    """
    assert captured, "graph_input was never captured"
    graph_input = captured.get("graph_input")
    assert graph_input is not None, "graph_input was never captured"
    assert "messages" in graph_input
    for msg in graph_input["messages"]:
        kwargs = getattr(msg, "additional_kwargs", None) or {}
        if kwargs.get("context_kind") == "task_context":
            return msg
    return None


# ============================================================
# Part A: HumanMessage injection in _process_message_with_tracking
# ============================================================


class TestTaskContextHumanMessageInjection:
    """Pin the basic shape of the ``task_context`` HumanMessage
    injected by
    :meth:`InstanceMessagingService._process_message_with_tracking`.

    The message is built at
    ``daemon/services/instance_messaging.py:3011-3020`` and appended
    at the end of ``persistent_context_msgs`` (after the stable
    context blocks) so the LangGraph ``add_messages`` reducer
    checkpoints it before the task message but after the stable
    context blocks.
    """

    async def test_task_context_injects_humanmessage_with_expected_fields(self):
        """With ``task_context`` set and ``is_retry=False`` a
        HumanMessage with the exact ``content``, ``id`` and
        ``additional_kwargs`` documented in the design lands in
        ``persistent_context_msgs``.

        Regression: the message id format ``task-context-{message_id}``
        is the stable handle the read path uses to identify the
        context block — losing it would break dedup across the
        checkpoint boundary.
        """
        task_context = (
            "[SYSTEM CONTEXT: Task Context]\n\n"
            "{\"topic\": \"review PR #42\"}"
        )

        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={},
            shared_context_injected=False,
            project_injected=True,
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
                    message="Please review PR #42.",
                    message_id="msg-1",
                    is_retry=False,
                    message_source="agent:leader",
                    task_context=task_context,
                )

        # Tightened: the body MUST have run and captured the graph_input.
        assert captured.get("graph_input") is not None, (
            "test must reach the body — if not, the assertions below are silent"
        )

        task_msg = _find_task_context_msg(captured)
        assert task_msg is not None, (
            "expected a HumanMessage with context_kind='task_context' to be "
            f"injected — got messages: {captured['graph_input']['messages']!r}"
        )

        # Content is the verbatim task_context string (no reformatting).
        assert task_msg.content == task_context, (
            f"task_context content must round-trip unchanged — got {task_msg.content!r}"
        )

        # id format is deterministic per message_id.
        assert task_msg.id == "task-context-msg-1", (
            f"expected id 'task-context-msg-1' — got {task_msg.id!r}"
        )

    async def test_no_task_context_no_injection(self):
        """With ``task_context=None`` no task_context HumanMessage
        is added to ``persistent_context_msgs``.

        Regression: a previous draft accidentally passed an empty
        string when ``task_context`` was ``None``, leaking a
        HumanMessage with an empty content. The ``if task_context and not is_retry``
        guard must keep the block empty in that case.
        """
        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={},
            shared_context_injected=False,
            project_injected=True,
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
                    task_context=None,
                )

        assert captured.get("graph_input") is not None
        task_msg = _find_task_context_msg(captured)
        assert task_msg is None, (
            "task_context=None must not inject a HumanMessage — "
            f"got {task_msg!r}"
        )

    async def test_empty_task_context_no_injection(self):
        """With ``task_context=""`` the falsy guard prevents injection.

        The Python truthiness check on the kwarg
        (``if task_context and not is_retry:``) treats ``""`` as
        falsy — no HumanMessage is created. This matches the
        "parent dispatched without context" case where the upstream
        formatter may produce an empty string by mistake.
        """
        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={},
            shared_context_injected=False,
            project_injected=True,
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
                    task_context="",  # explicit empty
                )

        assert captured.get("graph_input") is not None
        task_msg = _find_task_context_msg(captured)
        assert task_msg is None, (
            "task_context='' (falsy) must not inject a HumanMessage — "
            f"got {task_msg!r}"
        )

    async def test_retry_skips_task_context_injection(self):
        """On ``is_retry=True`` the task_context block is NOT
        re-injected — the message is already checkpointed on turn 1.

        Regression: a missing ``not is_retry`` guard would re-emit
        the same context block on every retry, causing the
        ``_emitted_message_content`` dedup to suppress the user
        message AND fill the conversation with duplicate context
        bubbles in the API response.
        """
        task_context = (
            "[SYSTEM CONTEXT: Task Context]\n\n"
            "{\"topic\": \"retry test\"}"
        )

        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={},
            shared_context_injected=False,
            project_injected=True,
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
                    message="retry please",
                    message_id="msg-1",
                    is_retry=True,  # <-- retry path
                    message_source="agent:leader",
                    task_context=task_context,
                )

        assert captured.get("graph_input") is not None
        task_msg = _find_task_context_msg(captured)
        assert task_msg is None, (
            "is_retry=True must skip task_context injection — "
            f"got {task_msg!r}"
        )

    async def test_task_context_appended_after_stable_context_blocks(self):
        """The task_context HumanMessage MUST sit AFTER the project /
        shared-context / skills stable context blocks — at the END of
        ``persistent_context_msgs``, right before the task message.

        The stable context blocks are identical across runs, so
        keeping them at the top of the persistent block maximises
        prompt-cache hit rate. Task context is dynamic (varies per
        message), so it is appended at the end of the persistent
        block, just before the user message.

        The hook in ``instance_messaging.py`` does
        ``persistent_context_msgs.append(_task_ctx_msg)`` after
        ``assemble_context_messages`` populates the list with the
        stable blocks — the position is critical because LangGraph's
        ``add_messages`` reducer preserves the supplied order, so the
        stable blocks will sit at the very start of the resulting
        ``state['messages']`` for every subsequent turn and the
        ``task_context`` will sit right before the task message.
        """
        from langchain_core.messages import HumanMessage as _HM

        # Two persistent context messages to be returned by the
        # orchestrator — they come FIRST, the task_context is appended
        # after them by the hook.
        ctx_project = _HM(
            content="[SYSTEM CONTEXT: Project]\nProject X.",
            id="ctx-project-1",
            additional_kwargs={"injected_message": True, "context_kind": "project"},
        )
        ctx_shared = _HM(
            content="[SYSTEM CONTEXT: Shared Context]\nkv=value",
            id="ctx-shared-1",
            additional_kwargs={
                "injected_message": True,
                "context_kind": "shared_context",
            },
        )

        task_context = "[SYSTEM CONTEXT: Task Context]\n\nreview PR"

        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={},
            shared_context_injected=False,
            project_injected=True,
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
                new=AsyncMock(return_value=([ctx_project, ctx_shared], [])),
            ):
                await svc._process_message_with_tracking(
                    instance_id="inst-1",
                    message="hello",
                    message_id="msg-1",
                    is_retry=False,
                    message_source="agent:leader",
                    task_context=task_context,
                )

        assert captured.get("graph_input") is not None
        messages = captured["graph_input"]["messages"]
        # messages layout: [ctx_project, ctx_shared, task_ctx, user_message]
        assert len(messages) == 4, (
            f"expected 4 messages (2 persistent + task_ctx + user), got {len(messages)}: "
            f"{messages!r}"
        )

        # Index 0 is the project context block (stable, first for cache).
        assert (getattr(messages[0], "additional_kwargs", None) or {}).get(
            "context_kind"
        ) == "project", (
            f"index 0 must be the project context block — got "
            f"{messages[0]!r}"
        )

        # Index 1 is the shared-context block.
        assert (getattr(messages[1], "additional_kwargs", None) or {}).get(
            "context_kind"
        ) == "shared_context", (
            f"index 1 must be the shared_context block — got "
            f"{messages[1]!r}"
        )

        # Index 2 is the task_context HumanMessage (dynamic, appended
        # after the stable blocks, before the user message).
        third = messages[2]
        third_kwargs = getattr(third, "additional_kwargs", None) or {}
        assert third_kwargs.get("context_kind") == "task_context", (
            f"index 2 must be the task_context HumanMessage — got "
            f"id={third.id!r} kwargs={third_kwargs!r}"
        )
        assert third.content == task_context

        # Index 3 is the user message (carries message_id).
        user_kwargs = getattr(messages[3], "additional_kwargs", None) or {}
        assert "context_kind" not in user_kwargs, (
            f"user message must NOT carry context_kind — got {user_kwargs!r}"
        )
        assert messages[3].id == "msg-1", (
            f"user message must carry message_id 'msg-1' — got {messages[3].id!r}"
        )

    async def test_task_context_id_format_is_deterministic(self):
        """The task_context message id is ``task-context-{message_id}``.

        A deterministic id (rather than a fresh ``uuid4`` like the
        other context blocks) is what makes the
        ``_emitted_message_content`` dedup table work — the read
        path's key is ``{instance_id}:context:{message_id}``.
        """
        task_context = "[SYSTEM CONTEXT: Task Context]\n\ntest"

        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={},
            shared_context_injected=False,
            project_injected=True,
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
                # Use a distinctive message_id to verify the
                # format's ``f"task-context-{message_id}"`` template
                # binds the right value.
                await svc._process_message_with_tracking(
                    instance_id="inst-1",
                    message="hello",
                    message_id="abc-def-12345",
                    is_retry=False,
                    message_source="agent:leader",
                    task_context=task_context,
                )

        assert captured.get("graph_input") is not None
        task_msg = _find_task_context_msg(captured)
        assert task_msg is not None
        assert task_msg.id == "task-context-abc-def-12345", (
            f"id format must be 'task-context-{{message_id}}' — got {task_msg.id!r}"
        )


# ============================================================
# Part B: Additional kwargs correctness
# ============================================================


class TestTaskContextAdditionalKwargs:
    """Pin the exact ``additional_kwargs`` payload on the task_context
    HumanMessage.

    The values are the canonical ADR-5 markers that the read-path
    guard (:func:`daemon.persistence._messages_have_context_block`)
    matches on, so any change here would silently break
    ``GET /messages`` for the new context kind.
    """

    async def test_additional_kwargs_exact_match(self):
        """``additional_kwargs`` MUST be exactly ``{"injected_message":
        True, "context_kind": "task_context"}`` — no extra keys, no
        missing keys.

        Regression: ``serialize_message`` and the
        ``_messages_have_context_block`` guard both key off
        ``injected_message`` and ``context_kind``. Adding a stray
        key would be cosmetic, but a missing or renamed key would
        break the read path silently.
        """
        task_context = "[SYSTEM CONTEXT: Task Context]\n\nreview"

        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={},
            shared_context_injected=False,
            project_injected=True,
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
                    task_context=task_context,
                )

        assert captured.get("graph_input") is not None
        task_msg = _find_task_context_msg(captured)
        assert task_msg is not None

        # Exact key set.
        assert set(task_msg.additional_kwargs.keys()) == {
            "injected_message",
            "context_kind",
        }, (
            f"additional_kwargs must be exactly {{injected_message, context_kind}} — "
            f"got {task_msg.additional_kwargs!r}"
        )
        # Exact values.
        assert task_msg.additional_kwargs["injected_message"] is True, (
            f"injected_message must be True — got "
            f"{task_msg.additional_kwargs['injected_message']!r}"
        )
        assert task_msg.additional_kwargs["context_kind"] == "task_context", (
            f"context_kind must be 'task_context' — got "
            f"{task_msg.additional_kwargs['context_kind']!r}"
        )

    async def test_context_kind_value_matches_constant(self):
        """The ``context_kind`` string MUST match the
        ``CONTEXT_KIND_TASK_CONTEXT`` constant in
        :mod:`daemon.services.context_messages` — divergence
        between the two would break the read-path guard.
        """
        from daemon.services.context_messages import CONTEXT_KIND_TASK_CONTEXT

        assert CONTEXT_KIND_TASK_CONTEXT == "task_context", (
            f"CONTEXT_KIND_TASK_CONTEXT constant must equal 'task_context' — "
            f"got {CONTEXT_KIND_TASK_CONTEXT!r}"
        )

        task_context = "[SYSTEM CONTEXT: Task Context]\n\nreview"

        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={},
            shared_context_injected=False,
            project_injected=True,
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
                    task_context=task_context,
                )

        assert captured.get("graph_input") is not None
        task_msg = _find_task_context_msg(captured)
        assert task_msg is not None
        # The value injected by the hook must match the constant
        # byte-for-byte — this is the only thing the read-path
        # guard's ``_CONTEXT_KINDS frozenset`` membership check
        # looks at.
        assert task_msg.additional_kwargs["context_kind"] == CONTEXT_KIND_TASK_CONTEXT, (
            f"injected context_kind must equal CONTEXT_KIND_TASK_CONTEXT — "
            f"got {task_msg.additional_kwargs['context_kind']!r} vs "
            f"{CONTEXT_KIND_TASK_CONTEXT!r}"
        )


# ============================================================
# Part C: Integration with existing context blocks
# ============================================================


class TestTaskContextWithOtherContextBlocks:
    """Pin the interaction between the ``task_context`` HumanMessage
    and the persistent context block produced by
    :func:`daemon.services.context_messages.assemble_context_messages`.

    The stable context blocks (project / shared-context / skills) MUST
    sit BEFORE the task_context HumanMessage — at the top of the
    persistent block — because they are identical across runs and thus
    maximise prompt-cache hit rate. The task_context is dynamic (varies
    per message), so it is appended at the END of the persistent block,
    right before the task message.
    """

    async def test_task_context_after_shared_context(self):
        """With task_context AND a shared_context block from
        ``assemble_context_messages``, the shared_context block sits
        at index 0 (stable block at the top for prompt-cache
        efficiency) and the task_context follows at index 1,
        appended after the stable block but before the user message.

        This mirrors the production layout the agent_node will see on
        every subsequent turn: the stable shared_context block leads,
        followed by the dynamic task_context, then the user message.
        """
        from langchain_core.messages import HumanMessage as _HM

        ctx_shared = _HM(
            content="[SYSTEM CONTEXT: Shared Context]\nkey=val",
            id="ctx-shared-1",
            additional_kwargs={
                "injected_message": True,
                "context_kind": "shared_context",
            },
        )
        task_context = "[SYSTEM CONTEXT: Task Context]\n\nreview PR #42"

        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={"key": "val"},  # enables the shared-context branch
            shared_context_injected=False,
            project_injected=True,
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
                new=AsyncMock(return_value=([ctx_shared], [])),
            ):
                await svc._process_message_with_tracking(
                    instance_id="inst-1",
                    message="hello",
                    message_id="msg-1",
                    is_retry=False,
                    message_source="agent:leader",
                    task_context=task_context,
                )

        assert captured.get("graph_input") is not None
        messages = captured["graph_input"]["messages"]
        # Layout: [shared_context, task_ctx, user_message]
        assert len(messages) == 3, (
            f"expected 3 messages (shared_context + task_ctx + user), got "
            f"{len(messages)}: {messages!r}"
        )

        # Index 0 is shared_context (stable block, first for cache).
        assert (
            getattr(messages[0], "additional_kwargs", None) or {}
        ).get("context_kind") == "shared_context"
        # Index 1 is task_context (appended after stable blocks).
        assert (
            getattr(messages[1], "additional_kwargs", None) or {}
        ).get("context_kind") == "task_context"
        # Index 2 is the user message.
        assert (
            getattr(messages[2], "additional_kwargs", None) or {}
        ).get("context_kind") is None
        assert messages[2].id == "msg-1"

    async def test_task_context_with_skill_block(self):
        """With task_context AND a skills block, the skills block sits
        at index 0 (stable block at the top) and the task_context
        follows (index 1), appended after the stable blocks.

        Regression: the ``append(_task_ctx_msg)`` call had to be
        placed AFTER ``assemble_context_messages`` returns (so the
        orchestrator-supplied list is the "base" and task_context
        goes at the end). This test pins the relative order across
        the skills path.
        """
        from langchain_core.messages import HumanMessage as _HM

        ctx_skills = _HM(
            content="[SYSTEM CONTEXT: Skills]\nskill=best-practice",
            id="ctx-skills-1",
            additional_kwargs={"injected_message": True, "context_kind": "skills"},
        )
        task_context = "[SYSTEM CONTEXT: Task Context]\n\nrefactor the auth module"

        captured: dict = {}
        graph = _make_capturing_graph(captured)
        manager = _make_manager(
            shared_context_kvs={},
            shared_context_injected=False,
            project_injected=True,
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
                new=AsyncMock(return_value=([ctx_skills], [])),
            ):
                await svc._process_message_with_tracking(
                    instance_id="inst-1",
                    message="hello",
                    message_id="msg-1",
                    is_retry=False,
                    message_source="agent:leader",
                    task_context=task_context,
                )

        assert captured.get("graph_input") is not None
        messages = captured["graph_input"]["messages"]
        # Layout: [skills, task_ctx, user_message]
        assert len(messages) == 3, (
            f"expected 3 messages (skills + task_ctx + user), got {len(messages)}: "
            f"{messages!r}"
        )
        # Index 0 is skills (stable block, first for cache).
        assert (
            getattr(messages[0], "additional_kwargs", None) or {}
        ).get("context_kind") == "skills", (
            f"index 0 must be skills — got {messages[0]!r}"
        )
        # Index 1 is task_context (appended after stable blocks).
        assert (
            getattr(messages[1], "additional_kwargs", None) or {}
        ).get("context_kind") == "task_context", (
            f"index 1 must be task_context — got {messages[1]!r}"
        )
        assert (
            getattr(messages[2], "additional_kwargs", None) or {}
        ).get("context_kind") is None
        assert messages[2].id == "msg-1"


# ============================================================
# Part D: Persistence layer recognition
# ============================================================


class TestTaskContextInPersistenceContextKinds:
    """Pin the read-path guard in :mod:`daemon.persistence` so the
    new ``task_context`` ``context_kind`` is recognised by
    :func:`daemon.persistence._messages_have_context_block`.

    The function is the early-skip guard that
    :func:`get_instance_messages` uses to detect a checkpointed
    context block — without ``"task_context"`` in the
    ``_CONTEXT_KINDS`` frozenset, a conversation that received a
    ``task_context`` HumanMessage would have a synthetic context
    block re-built on every ``GET /messages`` poll, causing the
    frontend to show the task_context twice (once from the
    checkpoint, once from the synthetic rebuild).
    """

    def test_messages_have_context_block_recognises_task_context(self):
        """A HumanMessage with ``additional_kwargs={"injected_message":
        True, "context_kind": "task_context"}`` MUST be detected by
        :func:`daemon.persistence._messages_have_context_block`.

        This is the behavioural check: we don't peek at the
        ``_CONTEXT_KINDS`` frozenset directly (it's a function-local
        constant); we feed the function a synthetic message and
        confirm it returns ``True``. The "no message" and
        "wrong context_kind" branches are covered as negative
        controls.
        """
        from daemon.persistence import _messages_have_context_block

        # Positive case: a HumanMessage with the canonical markers.
        task_msg = HumanMessage(
            content="[SYSTEM CONTEXT: Task Context]\n\nreview",
            id="task-context-msg-1",
            additional_kwargs={
                "injected_message": True,
                "context_kind": "task_context",
            },
        )
        assert _messages_have_context_block([task_msg]) is True, (
            "_messages_have_context_block must recognise context_kind='task_context'"
        )

        # Negative case 1: empty list — no messages at all.
        assert _messages_have_context_block([]) is False

        # Negative case 2: a regular user message (no injected_message).
        user_msg = HumanMessage(content="hello", id="user-1")
        assert _messages_have_context_block([user_msg]) is False, (
            "a plain user message must not be detected as a context block"
        )

        # Negative case 3: a message with injected_message but a
        # different context_kind (e.g. legacy "agent_context"). The
        # guard must not over-match.
        legacy_msg = HumanMessage(
            content="[SYSTEM CONTEXT: Agent Context]\nfoo",
            id="ctx-legacy-1",
            additional_kwargs={
                "injected_message": True,
                "context_kind": "agent_context",  # NOT a real kind
            },
        )
        assert _messages_have_context_block([legacy_msg]) is False, (
            "context_kind='agent_context' must not be detected as a "
            "recognised context block"
        )
