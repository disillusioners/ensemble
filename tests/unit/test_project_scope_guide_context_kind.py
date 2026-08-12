"""Unit tests for the C10 project-scope-guide context-kinds fix.

Mirrors ``tests/unit/test_blueprint_context_kind.py``. The
``_CONTEXT_KINDS`` frozenset lives inside
:func:`daemon.persistence._messages_have_context_block` as a
function-local constant. It MUST include ``"project_scope_guide"`` so
that project-scope-guide context messages are detected on checkpoint
reload — otherwise the synthetic rebuild path would re-build (and risk
duplicating) the scope-guide block on every ``GET /messages`` poll.

The behavioural test is the right shape here: feed the function a
synthetic ``HumanMessage`` carrying
``additional_kwargs={"injected_message": True, "context_kind":
"project_scope_guide"}`` and assert it returns ``True``. We don't peek
at the frozenset directly (it is function-local).

The membership test (constant introspection) is a simpler check that
the frozenset literally contains ``"project_scope_guide"``. We read
the source of the function and grep for the literal — robust to the
frozenset being a function-local constant.
"""

from __future__ import annotations

import inspect

import pytest
from langchain_core.messages import HumanMessage


def test_project_scope_guide_in_context_kinds_frozenset():
    """C10: the ``_CONTEXT_KINDS`` frozenset MUST contain ``"project_scope_guide"``.

    Reads the source of ``_messages_have_context_block`` and greps
    for the literal — robust to the frozenset being a function-local
    constant (no direct introspection is possible).
    """
    from daemon.persistence import _messages_have_context_block

    source = inspect.getsource(_messages_have_context_block)
    # The frozenset literal must include "project_scope_guide".
    assert '"project_scope_guide"' in source, (
        "C10: _CONTEXT_KINDS must include 'project_scope_guide' so "
        "scope-guide context messages are detected on checkpoint "
        "reload"
    )


def test_messages_have_context_block_recognises_project_scope_guide():
    """C10 behavioural check: a ``HumanMessage`` carrying
    ``context_kind="project_scope_guide"`` is detected by
    :func:`daemon.persistence._messages_have_context_block`.

    Without the allowlist entry, the function would return False
    and the synthetic rebuild path would re-emit the scope-guide
    block on every ``GET /messages`` poll, causing duplicate
    ``[SYSTEM CONTEXT: Project Scope Guide]`` blocks in the agent's
    context.
    """
    from daemon.persistence import _messages_have_context_block

    # Positive case: a context message with the canonical marker.
    msg = HumanMessage(
        content="[SYSTEM CONTEXT: Project Scope Guide]\n\nguide content",
        id="scope-guide-1",
        additional_kwargs={
            "injected_message": True,
            "context_kind": "project_scope_guide",
        },
    )
    assert _messages_have_context_block([msg]) is True, (
        "_messages_have_context_block must recognise "
        "context_kind='project_scope_guide'"
    )

    # Negative case: an empty list.
    assert _messages_have_context_block([]) is False

    # Negative case: a plain user message (no injected_message).
    user_msg = HumanMessage(content="hello", id="user-1")
    assert _messages_have_context_block([user_msg]) is False


def test_messages_have_context_block_negative_for_unknown_kind():
    """Negative case: a message with ``injected_message=True`` but a
    context_kind that is NOT in the allowlist (``"agent_context"``)
    must NOT be detected as a recognised context block.

    Mirrors the blueprint sibling test — the guard must not
    over-match.
    """
    from daemon.persistence import _messages_have_context_block

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


# ── Rebuild path: _build_context_dicts_for_response with system default ──


@pytest.mark.asyncio
async def test_rebuild_path_emits_exactly_one_scope_guide_block(monkeypatch):
    """The ``GET /messages`` rebuild path (``_build_context_dicts_for_response``)
    must emit EXACTLY ONE ``project_scope_guide`` block, never duplicates.

    Without the C10 allowlist fix in ``_CONTEXT_KINDS``, the synthetic
    rebuild path would re-build the scope-guide block on every poll
    even when the checkpoint already contains one — producing
    duplicate ``[SYSTEM CONTEXT: Project Scope Guide]`` blocks in
    the agent's context.

    We mock ``assemble_context_messages`` to return a single
    scope-guide tuple (the orchestrator's real output), then assert
    the serialized rebuild contains exactly one block with
    ``context_kind == "project_scope_guide"``.
    """
    from langchain_core.messages import HumanMessage

    from daemon import constants as consts
    from daemon.persistence import _build_context_dicts_for_response
    from daemon.services.context_messages import (
        build_project_scope_guide_message,
    )

    # Patch the module-level SYSTEM_DEFAULT_PROJECT_ID for the orchestrator
    # path (it's None at import; setting it exercises the ID branch).
    monkeypatch.setattr(consts, "SYSTEM_DEFAULT_PROJECT_ID", "default-uuid")

    # Build a single scope-guide message identical to what the real
    # orchestrator emits for the system-default branch.
    scope_guide_msg = build_project_scope_guide_message()

    async def _fake_assemble(*args: object, **kwargs: object) -> tuple[list, list]:
        # Return a single persistent message + no ephemerals.
        return ([scope_guide_msg], [])

    # Patch where ``assemble_context_messages`` is lazily imported in
    # ``daemon.persistence._build_context_dicts_for_response``.
    monkeypatch.setattr(
        "daemon.services.context_messages.assemble_context_messages",
        _fake_assemble,
    )

    # Stub instance_meta + agent_meta + manager.
    instance_meta = type("InstMeta", (), {
        "project_id": "default-uuid",
        "parent_id": None,
    })()

    agent_meta = type("AgentMeta", (), {
        "context_injection": None,
        "skill_injection": True,
    })()

    manager = type("Mgr", (), {
        "_instance_repository": None,
    })()

    ctx = {"instance_meta": instance_meta, "agent_meta": agent_meta}

    # The persisted checkpoint messages — must contain at least one
    # human/user message so the rebuild has a ``user_query`` to drive
    # the orchestrator (matches the real path).
    user_msg = HumanMessage(content="hello", id="user-1")
    messages = [user_msg]

    out = await _build_context_dicts_for_response(
        instance_id="inst-1",
        ctx=ctx,
        manager=manager,
        messages=messages,
    )

    # Exactly one context block — the scope guide.
    assert len(out) == 1, (
        f"_build_context_dicts_for_response must emit exactly one "
        f"context block, got {len(out)}: "
        f"{[d.get('context_kind') for d in out]}"
    )
    assert out[0]["context_kind"] == "project_scope_guide", (
        f"Expected context_kind='project_scope_guide', got "
        f"{out[0].get('context_kind')!r}"
    )
    assert "[SYSTEM CONTEXT: Project Scope Guide]" in out[0]["content"]
    # The synthetic rebuild stamps every block with is_synthetic=True
    # so the frontend can identify it.
    assert out[0]["is_synthetic"] is True
    assert out[0]["message_id"].startswith("synthetic-context-project_scope_guide-")