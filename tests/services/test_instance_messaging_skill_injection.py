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
    _dedup_merge_skill_ids,
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
    explicit_text: str | None = "[System Inject] explicit skill",
    explicit_skill_ids: list[str] | None = None,
    raise_on_explicit: Exception | None = None,
) -> MagicMock:
    """Build a :class:`SkillInjectionService` mock.

    The hook has two skill-injection entry points:

    * :meth:`SkillInjectionService.inject_skills` — the
      auto-load search path used by the first-attempt block.
      Returns ``(injection_text, skill_ids)`` by default.
      Pass ``injection_text=None`` to simulate "no skills
      matched" (the empty-result early-out). Pass
      ``raise_on_inject`` to simulate a transient search/DB
      error.
    * :meth:`SkillInjectionService.inject_explicit_skill` —
      the REPLACE path used by the ``<meta>`` tag block.
      Returns ``(explicit_text, explicit_skill_ids)`` by
      default. Same optional ``raise_on_explicit`` knob.

    Both paths share the :meth:`track_injection` metric hook.
    """
    if skill_ids is None:
        skill_ids = ["skill-A", "skill-B"]
    if explicit_skill_ids is None:
        explicit_skill_ids = ["skill-explicit-X"]
    svc = MagicMock()
    if raise_on_inject is not None:
        svc.inject_skills = AsyncMock(side_effect=raise_on_inject)
    else:
        svc.inject_skills = AsyncMock(
            return_value=(injection_text, skill_ids),
        )
    if raise_on_explicit is not None:
        svc.inject_explicit_skill = AsyncMock(side_effect=raise_on_explicit)
    else:
        svc.inject_explicit_skill = AsyncMock(
            return_value=(explicit_text, explicit_skill_ids),
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
    message: str = "hello",
) -> tuple[dict | None, MagicMock]:
    """Drive ``_process_message_with_tracking`` once with the
    given gate flags and return ``(captured_graph_input, manager)``.

    The manager mock is returned so tests can assert on
    ``manager._skill_injection_service.inject_skills.await_count``
    and similar. ``captured_graph_input`` is ``None`` if
    ``graph.astream`` was never invoked (e.g. when the function
    aborts early — not expected for the gate cases, but
    defensively typed).

    ``message`` defaults to ``"hello"`` for the existing gate
    tests; override to drive a payload that includes a
    ``<meta>`` directive so the explicit REPLACE branch is
    reached (Fix 1/2 tests).
    """
    captured: dict = {}
    graph = _make_capturing_graph(captured)
    manager = _make_manager(
        injection_service=injection_service,
        graph=graph,
    )

    with patch("daemon.registry.get_registry") as mock_get_registry:
        registry = MagicMock()
        # ``get_version`` returns ``None`` so the production code's
        # ``get_version() or get_resolved()`` fallback (S2/C1 fixes)
        # exercises the base-agent path these tests model — the
        # configured ``get_resolved`` return value wins.
        registry.get_version = MagicMock(return_value=None)
        registry.get_resolved = MagicMock(return_value=agent_meta)
        mock_get_registry.return_value = registry

        svc = _make_service(manager)
        await svc._process_message_with_tracking(
            instance_id="inst-1",
            message=message,
            message_id="msg-1",
            is_retry=is_retry,
            message_source=message_source,
        )

    return captured.get("graph_input"), manager


# ============================================================
# _build_graph_input — module-level helper (direct unit tests)
# ============================================================


class TestDedupMergeSkillIdsHelper:
    """Direct tests for :func:`_dedup_merge_skill_ids`.

    Centralizes the read-merge-write of ``last_injected_skill_ids`` so
    the BM25 persist and the auto-load persist share one implementation.
    These verify the dedup-merge contract directly, independent of the
    full messaging-path wiring.
    """

    @staticmethod
    def _repo(existing: list[str] | None = None) -> MagicMock:
        repo = MagicMock()
        inst = MagicMock()
        inst.instance_metadata = (
            {"last_injected_skill_ids": list(existing)} if existing else {}
        )
        repo.get.return_value = inst
        return repo

    def test_appends_new_ids_after_existing_preserving_order(self) -> None:
        repo = self._repo(existing=["a", "b"])
        _dedup_merge_skill_ids(repo, "inst-1", ["b", "c"])
        repo.set_metadata.assert_called_once_with(
            "inst-1", "last_injected_skill_ids", ["a", "b", "c"]
        )

    def test_dedups_repeats_within_new_ids(self) -> None:
        repo = self._repo(existing=[])
        _dedup_merge_skill_ids(repo, "inst-1", ["x", "x", "y"])
        repo.set_metadata.assert_called_once_with(
            "inst-1", "last_injected_skill_ids", ["x", "y"]
        )

    def test_drops_falsy_ids(self) -> None:
        repo = self._repo(existing=[])
        _dedup_merge_skill_ids(repo, "inst-1", ["", "k", None])  # type: ignore[list-item]
        repo.set_metadata.assert_called_once_with(
            "inst-1", "last_injected_skill_ids", ["k"]
        )

    def test_missing_instance_row_is_treated_as_empty(self) -> None:
        repo = MagicMock()
        repo.get.return_value = None
        _dedup_merge_skill_ids(repo, "inst-1", ["n"])
        repo.set_metadata.assert_called_once_with(
            "inst-1", "last_injected_skill_ids", ["n"]
        )

    def test_corrupted_metadata_value_is_tolerated(self) -> None:
        repo = MagicMock()
        inst = MagicMock()
        inst.instance_metadata = {"last_injected_skill_ids": "not-a-list"}
        repo.get.return_value = inst
        _dedup_merge_skill_ids(repo, "inst-1", ["m"])
        repo.set_metadata.assert_called_once_with(
            "inst-1", "last_injected_skill_ids", ["m"]
        )


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
        result = _build_graph_input(
            "hello",
            "msg-1",
            skill_msg,
            agent_meta=SimpleNamespace(context_injection_mode="legacy"),
        )

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
        result = _build_graph_input(
            "hello",
            "msg-1",
            skill_msg,
            agent_meta=SimpleNamespace(context_injection_mode="legacy"),
        )

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
        result = _build_graph_input(
            "hello",
            "msg-1",
            skill_msg,
            agent_meta=SimpleNamespace(context_injection_mode="legacy"),
        )

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
        result = _build_graph_input(
            "hello",
            "msg-1",
            skill_msg,
            agent_meta=SimpleNamespace(context_injection_mode="legacy"),
        )

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
            agent_meta=SimpleNamespace(
                agent_id="leader",
                skill_injection=True,
                context_injection_mode="legacy",
            ),
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

    # ── <meta> tag REPLACE gate (Fix 1 + 2, line ~2072) ──────

    async def test_meta_tag_skipped_on_completion_report(self) -> None:
        """CRITICAL Fix 1: ``<meta>`` REPLACE blocked on completion reports.

        A child agent's completion report may incidentally contain
        a ``<meta>`` directive (e.g. the child ran ``load_skill``
        during its work and the report echoes it). Triggering the
        REPLACE side-effect here would hijack the parent
        instance's skill state — finalizing the parent's existing
        skills as SUPERSEDED and injecting the child's skill into
        the parent's graph. The guard ``if is_completion_report
        or is_retry:`` (line 2073) short-circuits the entire
        REPLACE block.

        Post-fix note: with the new parent-dispatch gate at the
        top of ``_process_message_with_tracking``, the
        ``<meta>`` tag is **also** left intact in the user-visible
        message for ``internal_report:*`` sources — stripping
        would have leaked control-plane syntax to the child
        instance and created a hijack surface. The message the
        agent receives keeps the raw ``<meta>...</meta>`` text.
        The REPLACE-gate assertions (``inject_explicit_skill``
        not awaited) are unchanged — those are independent of
        the strip behaviour and still hold.

        Verification:

        * ``inject_explicit_skill`` is NEVER awaited — the
          REPLACE path is fully gated off.
        * No skill ``HumanMessage`` is prepended (the
          first-attempt ``inject_skills`` block also gates on
          the same completion-report flag).
        * The user message preserves the original ``<meta>``
          substring verbatim — ``parse_meta_tag`` is gated off
          on non-parent sources.
        """
        injection_service = _make_injection_service()
        message = 'task complete <meta>{"load_skill": "child-skill"}</meta>'

        captured_input, manager = await _invoke_hook(
            is_retry=False,
            message_source="internal_report:child-7",
            agent_meta=SimpleNamespace(agent_id="leader", skill_injection=True),
            injection_service=injection_service,
            message=message,
        )

        assert captured_input is not None
        msgs = captured_input["messages"]

        # No skill message at all — both the auto-load and
        # explicit REPLACE paths are short-circuited.
        assert len(msgs) == 1
        assert msgs[0].id == "msg-1"

        # ``internal_report:*`` is NOT a parent dispatch, so the
        # new parent-dispatch gate at the top of the method
        # leaves ``message`` untouched. The user sees the raw
        # payload with the ``<meta>...</meta>`` substring intact.
        assert msgs[0].content == (
            'task complete <meta>{"load_skill": "child-skill"}</meta>'
        )
        assert "<meta>" in msgs[0].content
        assert "child-skill" in msgs[0].content

        # CRITICAL: the REPLACE path must not be invoked.
        manager._skill_injection_service.inject_explicit_skill.assert_not_awaited()
        # The auto-load path is also gated off on completion
        # reports — guard against a regression that re-enables it.
        manager._skill_injection_service.inject_skills.assert_not_awaited()
        manager._skill_injection_service.track_injection.assert_not_called()

    async def test_meta_tag_skipped_on_retry(self) -> None:
        """CRITICAL Fix 2: ``<meta>`` REPLACE blocked on retry.

        On retry, the original message is replayed with the same
        ``<meta>`` directive. Re-running the REPLACE side-effect
        would create a duplicate SUPERSEDED record for every
        prior-injected skill on every retry attempt, skewing the
        completion-rate aggregation and forcing the orphan sweep
        to clean up the noise. The guard ``if is_completion_report
        or is_retry:`` (line 2073) short-circuits the REPLACE
        block before ``inject_explicit_skill`` is awaited.

        Post-fix note: the ``<meta>`` tag is also preserved in
        the user-visible content for ``telegram:*`` sources
        (not a parent dispatch). The strip behaviour and the
        REPLACE gate are independent — retry still skips the
        REPLACE side-effect, and the message that finally
        reaches the LangGraph state contains the literal
        ``<meta>...</meta>`` substring the user (or upstream
        system) sent. The fix's safety analysis explicitly
        called out that stripping on every inbound source was
        unsafe; this test now covers the new contract.

        Verification:

        * ``inject_explicit_skill`` is NEVER awaited.
        * No skill ``HumanMessage`` is prepended (retry also
          gates the first-attempt ``inject_skills`` block — the
          LangGraph ``add_messages`` reducer re-attaches the
          original skill message from the checkpoint).
        * The user message preserves the original ``<meta>``
          substring verbatim.
        """
        injection_service = _make_injection_service()
        message = 'retry <meta>{"load_skill": "skill-a"}</meta>'

        captured_input, manager = await _invoke_hook(
            is_retry=True,
            message_source="telegram:user-1",
            agent_meta=SimpleNamespace(agent_id="leader", skill_injection=True),
            injection_service=injection_service,
            message=message,
        )

        assert captured_input is not None
        msgs = captured_input["messages"]

        # Only the user message — no skill message prepended.
        assert len(msgs) == 1
        assert msgs[0].id == "msg-1"
        # ``telegram:user-1`` is NOT a parent dispatch, so the
        # new gate leaves ``message`` untouched. The raw
        # ``<meta>...</meta>`` substring reaches the graph.
        assert msgs[0].content == (
            'retry <meta>{"load_skill": "skill-a"}</meta>'
        )
        assert "<meta>" in msgs[0].content
        assert "skill-a" in msgs[0].content

        # CRITICAL: the REPLACE path must not be invoked on retry.
        manager._skill_injection_service.inject_explicit_skill.assert_not_awaited()
        manager._skill_injection_service.inject_skills.assert_not_awaited()


# ============================================================
# <meta> tag parent-dispatch gate (top-of-method strip)
# ============================================================


@pytest.mark.asyncio
class TestMetaTagParentDispatchGate:
    """Verify the parent-dispatch gate that controls whether
    ``parse_meta_tag`` is allowed to strip ``<meta>...</meta>``
    control blocks from the raw message at the top of
    ``_process_message_with_tracking`` (lines ~1596-1626 of
    ``daemon/services/instance_messaging.py``).

    Gate under test:

    * ``message_source`` starts with ``internal_agent:`` AND
      does NOT start with ``internal_agent:job_event:``
      → parent dispatch → ``parse_meta_tag`` runs, the
      ``<meta>...</meta>`` substring is removed from the
      user-visible message.
    * Anything else (``user``, ``api``, ``telegram:*``,
      ``internal_report:*``, ``internal_error_report:*``,
      ``internal_agent:job_event:*``, ``None``) → not a
      parent dispatch → ``parse_meta_tag`` does NOT run,
      the raw message (including any literal
      ``<meta>...</meta>`` substring) reaches the LangGraph
      state untouched.

    This carve-out is the **inverse** of the existing
    ``is_completion_report`` gate at the C3 REPLACE site
    (lines ~1716-1722): same set of ``internal_*`` prefixes,
    opposite selection. The two gates together pin down the
    intended semantics for every ``message_source`` value:

    * Parent dispatch (``internal_agent:<id>`` not
      job-event) → strip + REPLACE-eligible.
    * Internal ping (``internal_report:*`` /
      ``internal_error_report:*`` /
      ``internal_agent:job_event:*``) → leave raw +
      REPLACE-gated.
    * External source (``user`` / ``api`` / ``telegram:*`` /
      ``None``) → leave raw, REPLACE-eligible on the C3
      block's own retry/completion-report gates (which are
      False for external sources anyway).

    Every test in this class drives ``_invoke_hook`` with a
    message that contains a ``<meta>...</meta>`` substring
    and asserts on the resulting ``msgs[0].content``. The
    existing ``_make_injection_service`` helper signature is
    reused as-is.
    """

    # ── positive: parent dispatch strips the tag ──────────────

    async def test_meta_tag_stripped_for_parent_dispatch(self) -> None:
        """Happy path: ``message_source='internal_agent:abc-123'``
        is a parent-to-child dispatch (not a job-event ping), so
        ``parse_meta_tag`` runs. The ``<meta>...</meta>`` block
        is removed from the user-visible message and only the
        leading text ``hello`` reaches the LangGraph state.

        Uses ``skill_injection=False`` AND ``injection_service=None``
        so neither the auto-load ``inject_skills`` block nor the
        C3 REPLACE ``inject_explicit_skill`` block fires — these
        tests are about the strip gate only. With both side-effect
        paths gated off, ``msgs[0]`` is unambiguously the cleaned
        user message and the spec's ``msgs[0].content == 'hello'``
        assertion holds without ambiguity.
        """
        message = 'hello <meta>{"load_skill": "skill-x"}</meta>'

        captured_input, _manager = await _invoke_hook(
            is_retry=False,
            message_source="internal_agent:abc-123",
            agent_meta=SimpleNamespace(agent_id="leader", skill_injection=False),
            injection_service=None,  # gate off both auto-load and REPLACE
            message=message,
        )

        assert captured_input is not None
        msgs = captured_input["messages"]

        # ``injection_service=None`` (the ``getattr(..., None)``
        # fallback the production code uses) gates off BOTH the
        # first-attempt ``inject_skills`` block and the C3 REPLACE
        # ``inject_explicit_skill`` block, so there is no injected
        # skill message — only the cleaned user message.
        assert len(msgs) == 1
        assert msgs[0].id == "msg-1"

        # `parse_meta_tag` ran: the tag is gone and the
        # JSON blob's ``skill-x`` payload that lived inside it
        # is gone too.
        assert msgs[0].content == "hello"
        assert "<meta>" not in msgs[0].content
        assert "skill-x" not in msgs[0].content

    # ── negative: external sources preserve the tag ──────────

    async def test_meta_tag_not_stripped_for_user_message(self) -> None:
        """``message_source='user'`` is NOT a parent dispatch.
        ``parse_meta_tag`` is gated off and the raw payload —
        including the literal ``<meta>...</meta>`` substring
        and the ``skill-x`` payload inside it — reaches the
        graph verbatim. This is the fix's core safety property:
        arbitrary user content must never have its control-plane
        syntax rewritten.

        Uses ``skill_injection=False`` so the auto-load block
        doesn't prepend a skill message and ``msgs[0]`` is
        unambiguously the user message.
        """
        injection_service = _make_injection_service()
        message = 'hello <meta>{"load_skill": "skill-x"}</meta>'

        captured_input, _manager = await _invoke_hook(
            is_retry=False,
            message_source="user",
            agent_meta=SimpleNamespace(agent_id="leader", skill_injection=False),
            injection_service=injection_service,
            message=message,
        )

        assert captured_input is not None
        msgs = captured_input["messages"]

        assert len(msgs) == 1
        assert msgs[0].id == "msg-1"
        # Entire original payload preserved — ``parse_meta_tag``
        # did NOT run.
        assert msgs[0].content == message
        assert "<meta>" in msgs[0].content
        assert "</meta>" in msgs[0].content
        assert "skill-x" in msgs[0].content

    async def test_meta_tag_not_stripped_for_job_event(self) -> None:
        """Critical negative case: ``message_source='internal_agent:job_event:xyz'``
        shares the ``internal_agent:`` prefix with parent
        dispatches but is NOT one — the gate's second clause
        (job-event exclusion) must catch it. Misclassifying a
        job event as a parent dispatch would feed the C3
        REPLACE block a ``load_skill`` extracted from a system
        ping, hijacking the instance's skill state.

        Without the ``and not ...job_event:`` clause this test
        would fail: the strip would fire and the
        ``<meta>...</meta>`` substring would vanish.

        Uses ``skill_injection=False`` so the auto-load block
        doesn't prepend a skill message; the ``is_completion_report``
        gate above the auto-load block would already block it
        for this source, but ``skill_injection=False`` keeps
        the fixture shape consistent across all five tests.
        """
        injection_service = _make_injection_service()
        message = 'hello <meta>{"load_skill": "skill-x"}</meta>'

        captured_input, _manager = await _invoke_hook(
            is_retry=False,
            message_source="internal_agent:job_event:xyz",
            agent_meta=SimpleNamespace(agent_id="leader", skill_injection=False),
            injection_service=injection_service,
            message=message,
        )

        assert captured_input is not None
        msgs = captured_input["messages"]

        assert len(msgs) == 1
        assert msgs[0].id == "msg-1"
        assert msgs[0].content == message
        assert "<meta>" in msgs[0].content
        assert "skill-x" in msgs[0].content

    async def test_meta_tag_not_stripped_for_api_message(self) -> None:
        """``message_source='api'`` is an external source (HTTP
        / programmatic caller). Same preservation contract as
        ``user`` and ``telegram:*`` — the literal ``<meta>``
        substring reaches the graph untouched. API consumers
        may legitimately use angle-bracket-like payloads for
        their own data and we must not silently rewrite them.

        Uses ``skill_injection=False`` so the auto-load block
        doesn't prepend a skill message.
        """
        injection_service = _make_injection_service()
        message = 'hello <meta>{"load_skill": "skill-x"}</meta>'

        captured_input, _manager = await _invoke_hook(
            is_retry=False,
            message_source="api",
            agent_meta=SimpleNamespace(agent_id="leader", skill_injection=False),
            injection_service=injection_service,
            message=message,
        )

        assert captured_input is not None
        msgs = captured_input["messages"]

        assert len(msgs) == 1
        assert msgs[0].id == "msg-1"
        assert msgs[0].content == message
        assert "<meta>" in msgs[0].content
        assert "skill-x" in msgs[0].content

    async def test_meta_tag_not_stripped_for_none_source(self) -> None:
        """``message_source=None`` is the legacy default when
        the caller does not declare a source — e.g. internal
        jobs that pre-date the source-stamping convention.
        ``None`` must NOT trigger ``parse_meta_tag``: the
        ``startswith`` check on a ``None`` value would raise
        ``AttributeError`` (the gate's first clause guards
        against this) and even if it didn't, ``None`` is not a
        parent dispatch.

        Uses ``skill_injection=False`` so the auto-load block
        doesn't prepend a skill message.
        """
        injection_service = _make_injection_service()
        message = 'hello <meta>{"load_skill": "skill-x"}</meta>'

        captured_input, _manager = await _invoke_hook(
            is_retry=False,
            message_source=None,
            agent_meta=SimpleNamespace(agent_id="leader", skill_injection=False),
            injection_service=injection_service,
            message=message,
        )

        assert captured_input is not None
        msgs = captured_input["messages"]

        assert len(msgs) == 1
        assert msgs[0].id == "msg-1"
        assert msgs[0].content == message
        assert "<meta>" in msgs[0].content
        assert "skill-x" in msgs[0].content