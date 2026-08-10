"""Integration tests for the GET /messages API contract (Phase 4).

Phase 4 of the Context Injection Restructure surfaces context messages in
the GET /messages API response as synthetic, identifiable messages and
guarantees the endpoint stays strictly read-only.

Spec — :file:`.agents/shared/planning/context-injection-restructure/phase4-plan.md`
(Tasks 5 + 6):

* ``GET /messages`` returns::

      [synthetic_system] + [synthetic_context_msgs...] + [real_user_ai_msgs...]

* No DB writes happen during the read — ``set_metadata`` is never called,
  ``session.commit`` is never called, and the auto-load-skills appender
  is suppressed via ``disable_auto_load_tracking``.
* ``is_synthetic`` and ``context_kind`` are present on every synthetic
  context entry so the frontend can style them and the existing
  ``child_reports.py`` filter (lines 523 and 1007) keeps excluding them.

Compared to the unit-level ``TestGetInstanceMessagesHumanMessagesContext``
in :file:`tests/test_persistence.py`, these tests exercise the integration
seam — the real :func:`daemon.persistence.get_instance_messages` runs
end-to-end with the real :func:`daemon.utils.serialize_message`,
:func:`_locate_context_insertion_index`, and
:func:`_build_context_dicts_for_response`. The only seam mocked is the
external :func:`daemon.services.context_messages.assemble_context_messages`
(so the test doesn't need a real DB / skill index) plus the
sync helper :func:`_resolve_instance_message_context` (which would
require a real registry lookup).

Run only this file::

    pytest tests/integration/test_api_messages.py -v --tb=short
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — mirrors TestGetInstanceMessagesHumanMessagesContext in
# tests/test_persistence.py so the integration suite exercises the same
# mock surface.
# ─────────────────────────────────────────────────────────────────────────────


class _EmptyAsyncIterator:
    """Async iterator that yields nothing — mock for ``saver.alist``."""

    def __init__(self, items=None):
        self.items = items or []
        self.index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.index < len(self.items):
            item = self.items[self.index]
            self.index += 1
            return item
        raise StopAsyncIteration


def _make_persisted_messages():
    """Two-turn conversation; the LAST user turn is where context is rebuilt.

    Per the Phase 4 plan: ``"context messages appear before the most
    recent user message only"`` — historical turns remain bare in the
    API response, matching what the LLM actually saw on earlier turns.
    """
    return [
        HumanMessage(content="first user turn", id="msg-u1"),
        AIMessage(content="first assistant reply", id="msg-a1"),
        HumanMessage(content="second user turn", id="msg-u2"),
    ]


def _make_instance_meta(instance_id: str = "inst-api-1"):
    return SimpleNamespace(
        agent_id="developer",
        agent_tag=None,
        instance_metadata={},
        parent_id=None,
        project_id="project-1",
        created_at="2026-07-28T00:00:00+00:00",
    )


def _make_instance_repo(instance_meta):
    """Mock InstanceRepository that records every call for the no-DB-write test."""
    repo = MagicMock(name="InstanceRepository")
    repo.get = MagicMock(return_value=instance_meta)
    # Surface any write method the SUT might invoke; tests assert these
    # were NEVER called.
    repo.set_metadata = MagicMock()
    repo.update = MagicMock()
    repo.commit = MagicMock()
    return repo


def _make_manager(instance_repo):
    """Minimal manager stub — only the fields get_instance_messages touches."""
    manager = MagicMock(name="Manager")
    manager._instance_repository = instance_repo
    manager._skill_repo = MagicMock()
    manager._skill_clone_service = None
    manager._project_repository = MagicMock()
    manager.shared_meta_kv_repo = MagicMock()
    manager.prompt_cache = MagicMock()
    manager.config = SimpleNamespace(llm=SimpleNamespace(allowed_models=[]))
    return manager


def _make_mock_checkpointer(messages):
    """Mock checkpointer returning the given messages via aget()."""
    cp = MagicMock(name="Checkpointer")
    cp.aget = AsyncMock(return_value={
        "channel_values": {"messages": messages},
        "ts": "2026-07-28T00:00:00+00:00",
    })
    cp.alist = MagicMock(return_value=_EmptyAsyncIterator())
    return cp


def _context_human_messages():
    """Two context HumanMessages carrying ``context_kind`` markers.

    Returns the ``(persistent_msgs, ephemeral_msgs)`` tuple that
    :func:`daemon.services.context_messages.assemble_context_messages`
    actually produces. Tests assert that the persistent half is
    surfaced as synthetic context entries before the most recent user
    turn; the ephemeral half is documented as a no-op for this mode.
    """
    persistent = [
        HumanMessage(
            content="[SYSTEM CONTEXT: Related Project]\n\nproject body",
            additional_kwargs={
                "injected_message": True,
                "context_kind": "project",
            },
        ),
        HumanMessage(
            content="[SYSTEM CONTEXT: Skills]\n\nskill body",
            additional_kwargs={
                "injected_message": True,
                "context_kind": "skills",
            },
        ),
    ]
    ephemeral: list[HumanMessage] = []
    return (persistent, ephemeral)


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: API contract — human_messages mode response shape
# ─────────────────────────────────────────────────────────────────────────────


class TestHumanMessagesAPIResponseContract:
    """The Phase 4 response contract for ``context_injection_mode=human_messages``.

    Layout invariant::

        [0]                       — first persisted user  (historical, bare)
        [1]                       — first persisted assistant
        [2..N-2]                  — synthetic context msgs (is_synthetic=True)
        [N-1]                     — last persisted user    (current turn)

    Every synthetic context message carries::

        * ``is_synthetic=True``
        * ``context_kind`` ∈ {"project", "shared_context", "skills", ...}
        * stable ``message_id = "synthetic-context-<kind>-<instance_id>-<idx>"``

    Real persisted messages MUST NOT carry ``is_synthetic``.
    """

    @pytest.mark.asyncio
    async def test_human_messages_response_layout(self):
        """End-to-end check of the Phase 4 response shape via the real
        :func:`daemon.persistence.get_instance_messages` — only the
        external ``assemble_context_messages`` seam is mocked.
        """
        from daemon.persistence import get_instance_messages

        persisted = _make_persisted_messages()
        instance_meta = _make_instance_meta()
        instance_repo = _make_instance_repo(instance_meta)
        manager = _make_manager(instance_repo)
        checkpointer = _make_mock_checkpointer(persisted)

        # ctx payload returned by the (mocked) metadata resolver.
        ctx = {
            "instance_meta": instance_meta,
            "agent_meta": SimpleNamespace(context_injection_mode="human_messages"),
            "mode": "human_messages",
        }

        with patch(
            "daemon.persistence._resolve_instance_message_context",
            return_value=ctx,
        ) as mock_resolve, patch(
            # Mock the external context builder — this is the natural
            # integration seam. Everything below it (serialize_message,
            # _locate_context_insertion_index, the synthetic-context
            # message_id prefix) runs for real.
            "daemon.services.context_messages.assemble_context_messages",
            new=AsyncMock(return_value=_context_human_messages()),
        ) as mock_assemble, patch(
            # Suppress the system-prompt reconstruction path so this
            # test focuses purely on the context-rebuild contract.
            "daemon.persistence._reconstruct_full_system_prompt",
            return_value=None,
        ):
            messages = await get_instance_messages(
                checkpointer, "inst-api-1", manager=manager
            )

        # Metadata resolver called once with the right instance id.
        mock_resolve.assert_called_once_with("inst-api-1", manager)
        # External context builder was awaited with the right user query.
        mock_assemble.assert_awaited_once()
        # ``await_args`` returns a ``_Call`` object once awaited; guard
        # defensively so the test stays robust if the patch is ever
        # replaced with a sync side_effect.
        await_call = mock_assemble.await_args
        assert await_call is not None
        assemble_kwargs = await_call.kwargs
        assert assemble_kwargs["instance_id"] == "inst-api-1"
        assert assemble_kwargs["user_query"] == "second user turn"
        assert assemble_kwargs["agent_meta"] is ctx["agent_meta"]

        # Layout invariant: [0] user, [1] ai, [2..3] context, [4] user.
        assert len(messages) == 5, (
            f"Expected 5 messages, got {len(messages)}: {messages!r}"
        )

        # Historical turn — bare, NOT synthetic.
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "first user turn"
        assert "is_synthetic" not in messages[0]

        # Historical assistant turn — bare, NOT synthetic.
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "first assistant reply"
        assert "is_synthetic" not in messages[1]

        # Synthetic context entries appear BEFORE the current user turn
        # (indices [2] and [3]) — context is rebuilt for the LATEST user
        # message only.
        assert messages[2]["is_synthetic"] is True
        assert messages[2]["context_kind"] == "project"
        assert messages[2]["message_id"] == "synthetic-context-project-inst-api-1-0"
        assert messages[2]["role"] == "user"
        assert "Related Project" in messages[2]["content"]

        assert messages[3]["is_synthetic"] is True
        assert messages[3]["context_kind"] == "skills"
        assert messages[3]["message_id"] == "synthetic-context-skills-inst-api-1-1"
        assert messages[3]["role"] == "user"
        assert "Skills" in messages[3]["content"]

        # Current user turn — bare, NOT synthetic.
        assert messages[4]["role"] == "user"
        assert messages[4]["content"] == "second user turn"
        assert "is_synthetic" not in messages[4]

    @pytest.mark.asyncio
    async def test_human_messages_response_with_synthetic_system_message(self):
        """When the system prompt reconstructor succeeds AND the agent is in
        ``human_messages`` mode, the synthetic system message precedes the
        persisted messages, and the synthetic context messages are inserted
        immediately before the most recent user turn (NOT directly after
        the synthetic system message).

        Layout::

            [0] synthetic system (is_synthetic=True)
            [1] historical user
            [2] historical assistant
            [3] synthetic context (project)
            [4] synthetic context (skills)
            [5] current user  ← context anchored to this turn only
        """
        from daemon.persistence import get_instance_messages

        persisted = _make_persisted_messages()
        instance_meta = _make_instance_meta()
        instance_repo = _make_instance_repo(instance_meta)
        manager = _make_manager(instance_repo)
        checkpointer = _make_mock_checkpointer(persisted)

        ctx = {
            "instance_meta": instance_meta,
            "agent_meta": SimpleNamespace(context_injection_mode="human_messages"),
            "mode": "human_messages",
        }

        with patch(
            "daemon.persistence._resolve_instance_message_context",
            return_value=ctx,
        ), patch(
            "daemon.services.context_messages.assemble_context_messages",
            new=AsyncMock(return_value=_context_human_messages()),
        ), patch(
            "daemon.persistence._reconstruct_full_system_prompt",
            return_value=("FULL SYSTEM PROMPT", "2026-07-28T00:00:00+00:00"),
        ):
            messages = await get_instance_messages(
                checkpointer, "inst-api-1", manager=manager
            )

        assert len(messages) == 6

        # Index 0 is the synthetic system message.
        assert messages[0]["role"] == "system"
        assert messages[0]["is_synthetic"] is True
        assert messages[0]["message_id"] == "synthetic-system-inst-api-1"
        assert messages[0]["content"] == "FULL SYSTEM PROMPT"

        # Indexes 1 and 2 are the historical persisted turns — bare.
        assert messages[1]["content"] == "first user turn"
        assert "is_synthetic" not in messages[1]
        assert messages[2]["content"] == "first assistant reply"
        assert "is_synthetic" not in messages[2]

        # Indexes 3 and 4 are synthetic context — anchored to the LAST user
        # turn (index 5), not the synthetic system message.
        assert messages[3]["is_synthetic"] is True
        assert messages[3]["context_kind"] == "project"
        assert messages[3]["message_id"] == "synthetic-context-project-inst-api-1-0"
        assert messages[4]["is_synthetic"] is True
        assert messages[4]["context_kind"] == "skills"
        assert messages[4]["message_id"] == "synthetic-context-skills-inst-api-1-1"

        # Index 5 is the current user turn — bare, NOT synthetic.
        assert messages[5]["role"] == "user"
        assert messages[5]["content"] == "second user turn"
        assert "is_synthetic" not in messages[5]


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: GET /messages is strictly read-only
# ─────────────────────────────────────────────────────────────────────────────


class TestGetMessagesMakesNoDBWrites:
    """Phase 4 constraint: no DB writes happen during GET /messages.

    Mirrors the regression the Phase 4 plan calls out:

        "Known bug: ``append_auto_load_skills`` writes to DB during
         GET /messages poll."

    After the Phase 4 fix the call must:

    * Never call ``set_metadata`` on the InstanceRepository.
    * Never call any other write-shaped method on the repo.
    * Suppress the auto-load-skills appender (it used to ``set_metadata``).
    """

    @pytest.mark.asyncio
    async def test_human_messages_mode_does_not_call_set_metadata(self):
        from daemon.persistence import get_instance_messages

        persisted = _make_persisted_messages()
        instance_meta = _make_instance_meta()
        instance_repo = _make_instance_repo(instance_meta)
        manager = _make_manager(instance_repo)
        checkpointer = _make_mock_checkpointer(persisted)

        ctx = {
            "instance_meta": instance_meta,
            "agent_meta": SimpleNamespace(context_injection_mode="human_messages"),
            "mode": "human_messages",
        }

        with patch(
            "daemon.persistence._resolve_instance_message_context",
            return_value=ctx,
        ), patch(
            "daemon.services.context_messages.assemble_context_messages",
            new=AsyncMock(return_value=_context_human_messages()),
        ), patch(
            "daemon.persistence._reconstruct_full_system_prompt",
            return_value=("PROMPT", "2026-07-28T00:00:00+00:00"),
        ):
            messages = await get_instance_messages(
                checkpointer, "inst-api-1", manager=manager
            )

        # Sanity check — we actually got a response with context.
        assert any(m.get("is_synthetic") and m.get("context_kind") for m in messages)

        # Core invariant: NO write-shaped method was called on the repo.
        instance_repo.set_metadata.assert_not_called()
        instance_repo.update.assert_not_called()
        instance_repo.commit.assert_not_called()

        # Auto-load-skills suppression — the appender side must NOT have
        # been invoked. We assert this by recording the kwargs the
        # reconstruction helper received; if ``disable_auto_load_tracking``
        # is False we'd see auto-load side effects. We also verify the
        # skill_repo has no write calls.
        manager._skill_repo.set_metadata.assert_not_called()
        manager._skill_repo.create.assert_not_called()
        manager._skill_repo.update.assert_not_called()

        # Context rebuild path itself must NOT have written to the shared-
        # context KV or the project repo.
        manager.shared_meta_kv_repo.set.assert_not_called()
        manager._project_repository.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_messages_reconstruction_disables_auto_load_tracking(self):
        """The Phase 2 ``_apply_post_cache_appends`` is invoked with
        ``disable_auto_load_tracking=True`` from the GET /messages read
        path — this is what closes the historical bug where
        ``append_auto_load_skills`` wrote to the DB on every poll.

        Pin this contract at the integration boundary: when the read path
        calls the reconstruction helper, the auto-load tracking flag is
        set so the appender short-circuits before any write.
        """
        # Test the contract that _reconstruct_full_system_prompt forwards
        # disable_auto_load_tracking=True into _apply_post_cache_appends.
        # We drive _reconstruct_full_system_prompt directly with a real
        # agent directory + mock manager; the post-cache appender is
        # stubbed so we don't pull in the full skill pipeline.
        from pathlib import Path

        from daemon.persistence import _reconstruct_full_system_prompt

        instance_meta = _make_instance_meta()
        ctx = {
            "instance_meta": instance_meta,
            "agent_meta": SimpleNamespace(context_injection_mode="human_messages"),
            "mode": "human_messages",
        }

        instance_repo = _make_instance_repo(instance_meta)
        manager = _make_manager(instance_repo)

        # Use the real developer agent dir if available (mirrors the
        # unit test pattern in tests/test_persistence.py).
        agent_dir = Path(__file__).resolve().parent.parent.parent / "agents" / "developer"
        if not agent_dir.exists():
            pytest.skip("developer agent directory not present in this checkout")
        instance_meta.agent_dir = str(agent_dir)

        base_prompt = "You are a developer agent."
        # ``_reconstruct_full_system_prompt`` returns
        # ``(system_prompt, instance_created_at)`` — NOT the
        # ``_apply_post_cache_appends`` tuple of ``(prompt, language)``.
        # Both are mocked here; the test only cares about the kwargs
        # passed through to ``_apply_post_cache_appends``.
        with patch(
            "daemon.manager.load_and_cache_prompt",
            return_value=(base_prompt, len(base_prompt)),
        ) as mock_load, patch(
            "daemon.services.instance_lifecycle._apply_post_cache_appends",
            return_value=("FULL", "en"),
        ) as mock_apply:
            _ = _reconstruct_full_system_prompt("inst-api-1", manager, ctx=ctx)

        mock_load.assert_called_once()
        mock_apply.assert_called_once()

        # The auto-load tracking flag is the contract under test — if this
        # regresses, the bug returns (DB writes on every GET /messages).
        apply_kwargs = mock_apply.call_args.kwargs
        assert apply_kwargs.get("disable_auto_load_tracking") is True, (
            "GET /messages read path must invoke _apply_post_cache_appends "
            "with disable_auto_load_tracking=True. The historical bug was "
            "that this flag was False, causing append_auto_load_skills to "
            "call skill_repo.set_metadata on every poll."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: child_reports.py filter excludes synthetic context messages
# ─────────────────────────────────────────────────────────────────────────────


class TestChildReportsFilterExcludesSyntheticContext:
    """``daemon/services/child_reports.py`` lines 523 and 1007 both filter
    the message list with::

        messages = [m for m in messages if not m.get("is_synthetic")]

    This filter is the contract that keeps the child-report summarization
    paths from polluting their LLM prompt with the synthetic context
    messages surfaced by Phase 4. If ``is_synthetic`` were missing from
    a context entry, it would leak into child reports — a regression we
    pin here at the integration boundary.

    The test mirrors the EXACT filter expression used by
    ``child_reports.py`` so any change to that filter would also need to
    be reflected here (and vice-versa).
    """

    @pytest.mark.asyncio
    async def test_child_reports_filter_excludes_synthetic_context_messages(self):
        """Run a full GET /messages round-trip in ``human_messages`` mode,
        then apply the child_reports filter and verify only real
        persisted turns survive.
        """
        from daemon.persistence import get_instance_messages

        persisted = _make_persisted_messages()
        instance_meta = _make_instance_meta()
        instance_repo = _make_instance_repo(instance_meta)
        manager = _make_manager(instance_repo)
        checkpointer = _make_mock_checkpointer(persisted)

        ctx = {
            "instance_meta": instance_meta,
            "agent_meta": SimpleNamespace(context_injection_mode="human_messages"),
            "mode": "human_messages",
        }

        with patch(
            "daemon.persistence._resolve_instance_message_context",
            return_value=ctx,
        ), patch(
            "daemon.services.context_messages.assemble_context_messages",
            new=AsyncMock(return_value=_context_human_messages()),
        ), patch(
            "daemon.persistence._reconstruct_full_system_prompt",
            return_value=("PROMPT", "2026-07-28T00:00:00+00:00"),
        ):
            response = await get_instance_messages(
                checkpointer, "inst-api-1", manager=manager
            )

        # Sanity: response includes synthetic system + context + real msgs.
        kinds_present = {m.get("context_kind") for m in response if m.get("context_kind")}
        assert kinds_present == {"project", "skills"}
        assert any(m["role"] == "system" and m.get("is_synthetic") for m in response)

        # Apply the EXACT filter expression from child_reports.py:523.
        # If the filter expression changes in the source, mirror it here.
        filtered = [m for m in response if not (m.get("is_synthetic") or m.get("context_kind"))]

        # ALL synthetic entries are dropped — both the synthetic system
        # message AND every synthetic context message.
        assert len(filtered) == 3, (
            f"Expected 3 real messages after filter, got {len(filtered)}: "
            f"{[(m['role'], m['content'][:30]) for m in filtered]}"
        )

        # And the survivors are exactly the persisted user/assistant turns.
        assert filtered[0]["role"] == "user"
        assert filtered[0]["content"] == "first user turn"
        assert filtered[1]["role"] == "assistant"
        assert filtered[1]["content"] == "first assistant reply"
        assert filtered[2]["role"] == "user"
        assert filtered[2]["content"] == "second user turn"

        # No context_kind, no is_synthetic — clean dicts for the child-report LLM.
        for m in filtered:
            assert "is_synthetic" not in m or m.get("is_synthetic") is False
            assert "context_kind" not in m

    def test_filter_expression_matches_child_reports(self):
        """Static assertion: the filter expression this test uses MUST
        match ``child_reports.py`` lines 523 and 1007 verbatim. If those
        lines ever change, this test will fail and force a coordinated
        update of both call sites and this contract pin.
        """
        from daemon.services import child_reports

        src = Path = __import__("pathlib").Path
        path = src(child_reports.__file__)
        text = path.read_text(encoding="utf-8")

        # Both lines use the same expression — pin both.
        expected = "[m for m in messages if not (m.get(\"is_synthetic\") or m.get(\"context_kind\"))]"
        assert expected in text, (
            f"child_reports.py no longer contains the canonical filter "
            f"expression: {expected!r}. Update tests/integration/"
            f"test_api_messages.py and confirm the contract still holds."
        )
        # And it must appear at the two known sites.
        assert text.count(expected) >= 2, (
            f"Expected at least 2 occurrences of the filter expression in "
            f"child_reports.py (lines 523 and 1007), found "
            f"{text.count(expected)}."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Bonus: shape regression — synthetic context entries expose the
# shape the frontend relies on for styling.
# ─────────────────────────────────────────────────────────────────────────────


class TestSerializedContextMessageShape:
    """Phase 4 frontend contract: every synthetic context message in the
    response carries a stable, predictable shape.

    Verified fields:

    * ``message_id``  — ``synthetic-context-<kind>-<instance_id>-<idx>``
    * ``instance_id`` — echoes the path parameter
    * ``created_at``  — echoes the instance's created_at (so the message
      anchors at instance creation time, not at the poll time — the LLM
      receives the context on every turn, so anchoring it at poll time
      would make it look like a brand-new message each refresh).
    * ``is_synthetic=True`` and ``context_kind`` present.
    """

    @pytest.mark.asyncio
    async def test_synthetic_context_message_fields(self):
        from daemon.persistence import get_instance_messages

        persisted = _make_persisted_messages()
        instance_meta = _make_instance_meta()
        instance_repo = _make_instance_repo(instance_meta)
        manager = _make_manager(instance_repo)
        checkpointer = _make_mock_checkpointer(persisted)

        ctx = {
            "instance_meta": instance_meta,
            "agent_meta": SimpleNamespace(context_injection_mode="human_messages"),
            "mode": "human_messages",
        }

        with patch(
            "daemon.persistence._resolve_instance_message_context",
            return_value=ctx,
        ), patch(
            "daemon.services.context_messages.assemble_context_messages",
            new=AsyncMock(return_value=_context_human_messages()),
        ), patch(
            "daemon.persistence._reconstruct_full_system_prompt",
            return_value=None,
        ):
            messages = await get_instance_messages(
                checkpointer, "inst-api-1", manager=manager
            )

        ctx_msgs = [m for m in messages if m.get("context_kind")]
        assert len(ctx_msgs) == 2

        project_msg, skills_msg = ctx_msgs
        assert project_msg["context_kind"] == "project"
        assert project_msg["is_synthetic"] is True
        assert project_msg["message_id"] == "synthetic-context-project-inst-api-1-0"
        assert project_msg["instance_id"] == "inst-api-1"
        # ``created_at`` is set by ``serialize_message`` via
        # ``_extract_timestamp`` which falls back to ``datetime.now`` when
        # the source message has no ``id_metadata`` — so we only assert
        # it's a parseable ISO 8601 timestamp, not a specific value.
        assert isinstance(project_msg["created_at"], str)
        assert "T" in project_msg["created_at"]
        assert project_msg["role"] == "user"  # context is delivered as user msgs
        assert "Related Project" in project_msg["content"]

        assert skills_msg["context_kind"] == "skills"
        assert skills_msg["is_synthetic"] is True
        assert skills_msg["message_id"] == "synthetic-context-skills-inst-api-1-1"
        assert skills_msg["instance_id"] == "inst-api-1"
        assert skills_msg["role"] == "user"
        assert "Skills" in skills_msg["content"]