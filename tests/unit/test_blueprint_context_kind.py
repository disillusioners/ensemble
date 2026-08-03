"""Unit tests for the C10 context-kinds allowlist fix.

Phase 2 of the Project Blueprint evolution. The
``_CONTEXT_KINDS`` frozenset lives inside
:func:`daemon.persistence._messages_have_context_block` as a
function-local constant. It MUST include ``"blueprint"`` so that
blueprint context messages are detected on checkpoint reload —
otherwise the synthetic rebuild path would re-build (and risk
duplicating) the blueprint block on every GET /messages poll.

The behavioural test is the right shape here: feed the function
a synthetic ``HumanMessage`` carrying
``additional_kwargs={"injected_message": True, "context_kind":
"blueprint"}`` and assert it returns ``True``. We don't peek at
the frozenset directly (it is function-local).

The membership test (constant introspection) is a simpler check
that the frozenset literally contains ``"blueprint"``. We read
the source of the function and grep for the literal — robust to
the frozenset being a function-local constant.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage


def test_blueprint_in_context_kinds_frozenset():
    """C10: the ``_CONTEXT_KINDS`` frozenset MUST contain ``"blueprint"``.

    Reads the source of ``_messages_have_context_block`` and greps
    for the literal — robust to the frozenset being a function-local
    constant (no direct introspection is possible).
    """
    from daemon.persistence import _messages_have_context_block

    source = inspect.getsource(_messages_have_context_block)
    # The frozenset literal must include "blueprint".
    assert '"blueprint"' in source, (
        "C10: _CONTEXT_KINDS must include 'blueprint' so blueprint "
        "context messages are detected on checkpoint reload"
    )


def test_messages_have_context_block_recognises_blueprint():
    """C10 behavioural check: a ``HumanMessage`` carrying
    ``context_kind="blueprint"`` is detected by
    :func:`daemon.persistence._messages_have_context_block`.

    Without the allowlist entry, the function would return False
    and the synthetic rebuild path would re-emit the blueprint
    block on every GET /messages poll, causing duplicate
    blueprint blocks in the agent's context.
    """
    from daemon.persistence import _messages_have_context_block

    # Positive case: a context message with the canonical marker.
    blueprint_msg = HumanMessage(
        content="[SYSTEM CONTEXT: Project Blueprint]\n\ncore content",
        id="blueprint-msg-1",
        additional_kwargs={
            "injected_message": True,
            "context_kind": "blueprint",
        },
    )
    assert _messages_have_context_block([blueprint_msg]) is True, (
        "_messages_have_context_block must recognise "
        "context_kind='blueprint'"
    )

    # Negative case: an empty list.
    assert _messages_have_context_block([]) is False

    # Negative case: a plain user message (no injected_message).
    user_msg = HumanMessage(content="hello", id="user-1")
    assert _messages_have_context_block([user_msg]) is False

    # Negative case: a message with injected_message but an unknown
    # context_kind (e.g. legacy "agent_context"). The guard must not
    # over-match.
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
