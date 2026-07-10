"""Hook-level tests for the Phase 3 skill-injection path in
:meth:`InstanceMessagingService._process_message_with_tracking`.

The Phase 3 review flagged that while the
:class:`SkillInjectionService` unit tests were comprehensive
(``tests/services/test_skill_injection_service.py`` — 40/40
pass), no tests covered the **gating logic at the hook level**.
This file fills that gap by exercising the three gates the hook
enforces between lines 1740 and 1818 of
``daemon/services/instance_messaging.py``:

1. ``is_retry=True`` → skip injection. LangGraph's
   ``add_messages`` reducer preserves the original skill message
   from the first attempt's checkpoint, so injecting again on
   retry would duplicate.
2. ``is_completion_report=True`` (i.e. ``message_source`` starts
   with ``internal_report:``, ``internal_error_report:``, or
   ``internal_agent:job_event:``) → skip injection. These are
   internal pings, not real user prompts.
3. ``agent_meta.skill_injection`` not truthy (or
   ``agent_meta is None``, or the manager has no
   ``_skill_injection_service`` attribute) → skip injection.
   Skill injection is opt-in per agent and degrades gracefully
   when the manager predates Phase 3.

The helper :func:`_build_graph_input` (the consumer side of the
injection pipeline) is also tested directly here for the parts
the existing tests don't cover: that the skill message id is a
valid UUID and distinct from ``message_id``.

Implementation strategy
-----------------------

``_process_message_with_tracking`` is a 600-line method that
streams graph events, manages checkpoints, and dispatches SSE
updates. We don't try to exercise all of that. Instead we wire
every dependency to a mock, then capture the ``graph_input`` dict
the hook hands to ``graph.astream(...)`` and assert on its
``messages`` list.

Every gate test follows the same shape:

* build a manager mock with controlled
  ``_skill_injection_service`` / instance metadata
* patch ``daemon.registry.get_registry`` to return a registry
  whose ``get_resolved`` returns a controlled ``AgentMetadata``
* patch the graph's ``astream`` to capture ``graph_input`` and
  immediately end iteration
* invoke ``_process_message_with_tracking`` with the relevant
  flags and assert on the captured input
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from daemon.services.instance_messaging import (
    InstanceMessagingService,
    _build_graph_input,
)


# ============================================================
# Helpers
# ============================================================


def _make_capturing_graph(captured: dict) -> MagicMock:
    """Build a LangGraph mock whose ``astream`` captures the
    ``graph_input`` it receives, then immediately ends iteration
    so the surrounding ``async for event in graph.astream(...)``
    in ``_process_message_with_tracking`` exits cleanly.

    Tests assert against ``captured["graph_input"]``.
    """

    async def fake_astream(*args, **kwargs):
        # ``graph_input`` is the first positional arg in production;
        # capture both positional and keyword forms defensively.
        if args:
            captured["graph_input"] = args[0]
        elif "graph_input" in kwargs:
            captured["graph_input"] = kwargs["graph_input"]
        # Empty async generator — return immediately, no events.
        return
        yield  # pragma: no cover — sentinel for async-generator fn

    graph = MagicMock()
    graph.astream = fake_astream
    return graph


@asynccontextmanager
async def _null_semaphore():
    """Async context manager stand-in for ``manager._llm_semaphore``."""
    yield


def _make_injection_service(
    *,
    injection_text: str | None = "[System Inject] skill context",
    skill_ids: list[str] | None = None,
    raise_on_inject: Exception | None = None,
) -> MagicMock:
    """Build a :class:`SkillInjectionService` mock.

    ``inject_skills`` returns ``(injection_text, skill_ids)`` by
    default. Pass ``injection_text=None`` to simulate "no skills
    matched" (the empty-result early-out). Pass ``raise_on_inject``
    to simulate a transient search/DB error.
    """
    if skill_ids is None:
        skill_ids = ["skill-A", "skill-B"]
    svc = MagicMock()
    if raise_on_inject is not None:
        svc.inject_skills = AsyncMock(side_effect=raise_on_inject)
    else:
        svc.inject_skills = AsyncMock(
            return_value=(injection_text, skill_ids),
        )
    svc.track_injection = MagicMock()
    return svc


def _make_manager(
    *,
    injection_service: object = None,
    graph: MagicMock | None = None,
    agent_id: str = "leader",
    project_injected: bool = True,
) -> MagicMock:
    """Build a manager mock with every side effect
    ``_process_message_with_tracking`` reads or writes during the
    skill-injection hook wired to a stub.

    ``injection_service=None`` is treated as "the manager does
    not have a ``_skill_injection_service`` attribute" — exactly
    the production behaviour when the manager was built without
    ``skill_evolution`` config (the hook's ``getattr(..., None)``
    degrades to a no-op in that case).
    """
    instance_meta = SimpleNamespace(
        instance_id="inst-1",
        agent_id=agent_id,
        instance_metadata={"project_id": "proj-1", "project_injected": project_injected},
    )

    manager = MagicMock()
    manager.config.limits.graph_recursion_limit = 50
    manager.config.compaction = MagicMock()  # unused by the hook
    manager.get_instance = AsyncMock(return_value=graph)
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=instance_meta)
    manager._instance_repository.set_metadata = MagicMock(return_value=None)
    manager._live_hub = MagicMock()
    manager._live_hub.stream_message = AsyncMock()
    manager._queue_repository = MagicMock()  # used by ActivityCallbackHandler
    manager._graph_tasks = {}
    manager.source_dispatcher = None  # skip progressive-dispatch branch
    manager._llm_semaphore = _null_semaphore()

    # Set the attribute explicitly so ``getattr(manager, "_skill_injection_service", None)``
    # returns the configured value (including ``None`` for the no-attribute path).
    manager._skill_injection_service = injection_service
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


async def _invoke_hook(
    *,
    is_retry: bool = False,
    message_source: str | None = None,
    agent_meta: SimpleNamespace | None,
    injection_service: object = None,
) -> tuple[dict | None, MagicMock]:
    """Drive ``_process_message_with_tracking`` once with the
    given gate flags and return ``(captured_graph_input, manager)``.

    The manager mock is returned so tests can assert on
    ``manager._skill_injection_service.inject_skills.await_count``
    and similar. ``captured_graph_input`` is ``None`` if
    ``graph.astream`` was never invoked (e.g. when the function
    aborts early — not expected for the gate cases, but
    defensively typed).
    """
    captured: dict = {}
    graph = _make_capturing_graph(captured)
    manager = _make_manager(
        injection_service=injection_service,
        graph=graph,
    )

    with patch("daemon.registry.get_registry") as mock_get_registry:
        registry = MagicMock()
        registry.get_resolved = MagicMock(return_value=agent_meta)
        mock_get_registry.return_value = registry

        svc = _make_service(manager)
        await svc._process_message_with_tracking(
            instance_id="inst-1",
            message="hello",
            message_id="msg-1",
            is_retry=is_retry,
            message_source=message_source,
        )

    return captured.get("graph_input"), manager


# ============================================================
# _build_graph_input — module-level helper (direct unit tests)
# ============================================================


class TestBuildGraphInputHelper:
    """Direct tests for :func:`_build_graph_input`.

    The helper is the consumer side of the skill-injection
    pipeline: :class:`SkillInjectionService` produces the
    ``HumanMessage``, this helper prepends it to the graph input.
    We verify the parts the existing ``test_skill_injection_service``
    suite does not cover — most importantly that the skill
    message id is a valid UUID **and** distinct from
    ``message_id`` (LangGraph's ``add_messages`` reducer uses the
    id for deduplication and would dedupe one of them away if
    they matched).
    """

    def test_no_skill_msg_returns_user_message_only(self) -> None:
        """No injection → single-element list, user ``HumanMessage``
        carries the caller's ``message_id`` so ``add_messages``
        can dedupe across retries.
        """
        result = _build_graph_input("hello", "msg-1", None)

        assert "messages" in result
        assert len(result["messages"]) == 1
        user_msg = result["messages"][0]
        assert isinstance(user_msg, HumanMessage)
        assert user_msg.id == "msg-1"
        assert user_msg.content == "hello"

    def test_with_skill_msg_prepends_in_order(self) -> None:
        """Skill message comes first so the agent reads skill
        context before the user prompt (mirrors the
        system-context-then-user-input layout the rest of the
        codebase uses).
        """
        skill_msg = HumanMessage(content="[System Inject] skill", id="skill-id-1")
        result = _build_graph_input("hello", "msg-1", skill_msg)

        assert len(result["messages"]) == 2
        assert result["messages"][0] is skill_msg
        assert result["messages"][1].id == "msg-1"
        assert result["messages"][1].content == "hello"

    def test_skill_msg_id_is_uuid_string(self) -> None:
        """Production sets ``id=str(uuid.uuid4())`` on the skill
        ``HumanMessage`` (line 1802). Verify the helper accepts
        that and the id round-trips through ``uuid.UUID()``.
        """
        skill_msg = HumanMessage(content="[System Inject] skill", id=str(uuid.uuid4()))
        result = _build_graph_input("hello", "msg-1", skill_msg)

        extracted_id = result["messages"][0].id
        # Round-trip — any non-UUID garbage raises ``ValueError``.
        parsed = uuid.UUID(extracted_id)
        assert str(parsed) == extracted_id

    def test_skill_msg_id_distinct_from_message_id(self) -> None:
        """The skill message id MUST NOT match the user message
        id. LangGraph's ``add_messages`` reducer uses the id as
        the dedup key; a collision would cause one of the two
        messages to silently vanish from the conversation.
        """
        skill_id = str(uuid.uuid4())
        skill_msg = HumanMessage(content="[System Inject]", id=skill_id)
        result = _build_graph_input("hello", "msg-1", skill_msg)

        msgs = result["messages"]
        skill_actual_id = msgs[0].id
        user_msg_id = msgs[1].id
        assert skill_actual_id != user_msg_id
        assert skill_actual_id == skill_id
        assert user_msg_id == "msg-1"

    def test_skill_msg_id_is_uuid4(self) -> None:
        """Production uses ``uuid.uuid4()`` (line 1802). Verify
        the id parses as version 4 — a future regression to
        ``uuid1`` or ``uuid3`` would change the variant/version
        bits and break the contract.
        """
        skill_msg = HumanMessage(content="skill", id=str(uuid.uuid4()))
        result = _build_graph_input("hello", "msg-1", skill_msg)

        parsed = uuid.UUID(result["messages"][0].id)
        assert parsed.version == 4

    def test_multimodal_content_passes_through(self) -> None:
        """``_build_graph_input`` accepts content as a list of
        text + image blocks — verify the list form is preserved
        verbatim rather than stringified.
        """
        content = [
            {"type": "text", "text": "describe this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
        ]
        result = _build_graph_input(content, "msg-mm", None)

        assert len(result["messages"]) == 1
        assert result["messages"][0].content == content


# ============================================================
# Hook-level gate tests — _process_message_with_tracking
# ============================================================


@pytest.mark.asyncio
class TestSkillInjectionGates:
    """Verify the three gates the hook enforces inside
    ``_process_message_with_tracking`` between lines 1740 and
    1818 of ``daemon/services/instance_messaging.py``.

    Gate matrix under test:

    * ``is_retry=True`` → no skill message (the checkpoint
      already has it).
    * ``is_completion_report=True`` → no skill message
      (completion / error / job-event report, not a user prompt).
    * ``agent_meta.skill_injection`` is False or missing → no
      skill message (opt-in agent).
    * ``agent_meta is None`` → no skill message (unknown agent).
    * ``_skill_injection_service`` attribute missing → no skill
      message (manager built without ``skill_evolution`` config).
    * All gates pass → skill message prepended to ``graph_input``,
      ``track_injection`` called for Phase 4 metrics.
    * ``inject_skills`` raising → graceful fallback to user-only.
    * ``inject_skills`` returning ``(None, [])`` → no prepended
      message (empty-result early-out).
    """

    # ── is_retry gate ────────────────────────────────────────

    async def test_retry_skips_injection(self) -> None:
        """``is_retry=True`` short-circuits the entire
        ``if not is_retry:`` block, so ``_skill_injection_msg``
        stays ``None``. The LangGraph ``add_messages`` reducer
        will re-attach the original skill message from the prior
        attempt's checkpoint — adding it again here would
        duplicate.
        """
        injection_service = _make_injection_service()
        captured_input, manager = await _invoke_hook(
            is_retry=True,
            message_source=None,
            agent_meta=SimpleNamespace(agent_id="leader", skill_injection=True),
            injection_service=injection_service,
        )

        assert captured_input is not None
        msgs = captured_input["messages"]
        assert len(msgs) == 1, "retry must NOT prepend a skill message"
        assert msgs[0].id == "msg-1"
        assert msgs[0].content == "hello"
        # ``inject_skills`` must not be awaited at all on retry.
        manager._skill_injection_service.inject_skills.assert_not_awaited()
        manager._skill_injection_service.track_injection.assert_not_called()

    # ── is_completion_report gate (internal_* sources) ────────

    async def test_completion_report_skips_injection(self) -> None:
        """``message_source='internal_report:...'`` flags a
        child-instance completion report. The agent isn't
        actually responding to a user, so injecting skill
        context would be wasted.
        """
        injection_service = _make_injection_service()
        captured_input, manager = await _invoke_hook(
            is_retry=False,
            message_source="internal_report:child-42",
            agent_meta=SimpleNamespace(agent_id="leader", skill_injection=True),
            injection_service=injection_service,
        )

        assert captured_input is not None
        msgs = captured_input["messages"]
        assert len(msgs) == 1
        assert msgs[0].id == "msg-1"
        manager._skill_injection_service.inject_skills.assert_not_awaited()

    @pytest.mark.parametrize(
        "source",
        [
            "internal_error_report:child-7",
            "internal_agent:job_event:job-99",
        ],
    )
    async def test_internal_sources_skip_injection(self, source: str) -> None:
        """All three ``internal_``-prefixed sources (report,
        error-report, agent-job-event) skip injection — they're
        pings, not user prompts.
        """
        injection_service = _make_injection_service()
        captured_input, manager = await _invoke_hook(
            is_retry=False,
            message_source=source,
            agent_meta=SimpleNamespace(agent_id="leader", skill_injection=True),
            injection_service=injection_service,
        )

        assert captured_input is not None
        msgs = captured_input["messages"]
        assert len(msgs) == 1
        assert msgs[0].id == "msg-1"
        manager._skill_injection_service.inject_skills.assert_not_awaited()

    # ── agent_meta.skill_injection gate (opt-in) ─────────────

    async def test_agent_without_skill_injection_flag_skipped(self) -> None:
        """``agent_meta.skill_injection=False`` → opt-out. The
        hook respects the per-agent gate so the cost of the
        search is paid only by agents that want it.
        """
        injection_service = _make_injection_service()
        captured_input, manager = await _invoke_hook(
            is_retry=False,
            message_source=None,
            agent_meta=SimpleNamespace(agent_id="leader", skill_injection=False),
            injection_service=injection_service,
        )

        assert captured_input is not None
        assert len(captured_input["messages"]) == 1
        manager._skill_injection_service.inject_skills.assert_not_awaited()

    async def test_agent_meta_missing_skill_injection_attr_skipped(self) -> None:
        """Defensive: ``getattr(agent_meta, "skill_injection", False)``
        treats an ``AgentMetadata`` without the attribute as
        opt-out. The hook must not raise ``AttributeError``.
        """
        agent_meta = SimpleNamespace(agent_id="leader")  # no skill_injection attr
        injection_service = _make_injection_service()
        captured_input, manager = await _invoke_hook(
            is_retry=False,
            message_source=None,
            agent_meta=agent_meta,
            injection_service=injection_service,
        )

        assert captured_input is not None
        assert len(captured_input["messages"]) == 1
        manager._skill_injection_service.inject_skills.assert_not_awaited()

    async def test_no_agent_meta_skips_injection(self) -> None:
        """``registry.get_resolved`` returned ``None`` (unknown
        agent id). The hook's ``if agent_meta and ...`` check
        short-circuits — no per-agent config means no injection.
        """
        injection_service = _make_injection_service()
        captured_input, manager = await _invoke_hook(
            is_retry=False,
            message_source=None,
            agent_meta=None,
            injection_service=injection_service,
        )

        assert captured_input is not None
        assert len(captured_input["messages"]) == 1
        manager._skill_injection_service.inject_skills.assert_not_awaited()

    # ── missing _skill_injection_service attribute (graceful) ─

    async def test_no_injection_service_attribute_skips_injection(self) -> None:
        """``getattr(self._manager, "_skill_injection_service", None)``
        — managers built without ``skill_evolution`` config (or
        before Phase 3 wired it in) degrade to a no-op rather
        than raising ``AttributeError``.

        We explicitly set the attribute to ``None`` (the default
        ``getattr`` fallback) to simulate the "manager predates
        Phase 3" case.
        """
        injection_service = _make_injection_service()
        captured_input, manager = await _invoke_hook(
            is_retry=False,
            message_source=None,
            agent_meta=SimpleNamespace(agent_id="leader", skill_injection=True),
            injection_service=None,  # getattr fallback value
        )

        assert captured_input is not None
        assert len(captured_input["messages"]) == 1
        # The injection service was never awaited — its absence
        # is enough to skip the whole block.
        injection_service.inject_skills.assert_not_awaited()

    # ── happy path: all gates pass → prepended skill message ──

    async def test_all_gates_pass_prepends_skill_message(self) -> None:
        """Happy path — first attempt, real user message,
        opt-in agent, registered service. ``inject_skills`` is
        awaited and the resulting ``HumanMessage`` appears at
        index 0 of the graph input.
        """
        injection_text = "[System Inject] skill-A\nskill-B"
        injection_service = _make_injection_service(
            injection_text=injection_text,
            skill_ids=["skill-A", "skill-B"],
        )
        captured_input, manager = await _invoke_hook(
            is_retry=False,
            message_source="telegram:user-42",
            agent_meta=SimpleNamespace(agent_id="leader", skill_injection=True),
            injection_service=injection_service,
        )

        assert captured_input is not None
        msgs = captured_input["messages"]
        assert len(msgs) == 2, "skill message must be prepended"

        # Skill message first.
        skill_msg = msgs[0]
        assert isinstance(skill_msg, HumanMessage)
        assert skill_msg.content == injection_text
        # The skill id is a valid UUID AND not the user message_id.
        parsed = uuid.UUID(skill_msg.id)
        assert str(parsed) == skill_msg.id
        assert parsed.version == 4
        assert skill_msg.id != "msg-1"

        # User message second.
        assert msgs[1].id == "msg-1"
        assert msgs[1].content == "hello"

        # Phase 4 metrics attribution must be recorded.
        manager._skill_injection_service.inject_skills.assert_awaited_once()
        manager._skill_injection_service.track_injection.assert_called_once_with(
            "inst-1",
            "msg-1",
            ["skill-A", "skill-B"],
        )

    # ── graceful failure: inject_skills raises ───────────────

    async def test_inject_skills_raising_falls_back_to_user_only(self) -> None:
        """A transient DB / search error must not block the
        user message. The hook wraps the entire block in
        ``try/except`` and sets ``_skill_injection_msg = None``
        on any exception (line 1814-1818).
        """
        injection_service = _make_injection_service(
            raise_on_inject=RuntimeError("simulated DB outage"),
        )
        captured_input, manager = await _invoke_hook(
            is_retry=False,
            message_source="telegram:user-42",
            agent_meta=SimpleNamespace(agent_id="leader", skill_injection=True),
            injection_service=injection_service,
        )

        # No skill message → only the user message in the list.
        assert captured_input is not None
        msgs = captured_input["messages"]
        assert len(msgs) == 1
        assert msgs[0].id == "msg-1"
        assert msgs[0].content == "hello"

        # ``track_injection`` is only called on the success path
        # with a non-empty ``injection_text`` — the exception
        # path must not call it.
        manager._skill_injection_service.track_injection.assert_not_called()

    # ── empty-result early-out: inject_skills returns (None, []) ─

    async def test_inject_skills_returning_none_skips_prepend(self) -> None:
        """``inject_skills`` returned ``(None, [])`` — no
        ``injection_text`` → no prepended ``HumanMessage``.
        Same effect as the gates: only the user message
        reaches the graph.
        """
        injection_service = _make_injection_service(
            injection_text=None,
            skill_ids=[],
        )
        captured_input, manager = await _invoke_hook(
            is_retry=False,
            message_source="telegram:user-42",
            agent_meta=SimpleNamespace(agent_id="leader", skill_injection=True),
            injection_service=injection_service,
        )

        assert captured_input is not None
        msgs = captured_input["messages"]
        assert len(msgs) == 1
        assert msgs[0].id == "msg-1"
        # ``track_injection`` is gated by ``if injection_text:`` —
        # the empty result path must not call it.
        manager._skill_injection_service.track_injection.assert_not_called()