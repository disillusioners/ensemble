"""Integration pin (W9) — synthetic-context id enumeration order on the
``GET /messages`` read path.

kv-ambient-awareness-fix C2, phase3-plan Test Strategy §5c: the id mint
at ``daemon/persistence.py:949`` (
``synthetic-context-{context_kind}-{instance_id}-{idx}``, enumerate
region :937-949) must satisfy — for a FIXED context-message list:

    each ``{idx}`` suffix == the message's position in the returned
    ``GET /messages`` array (enumeration order = array order; no
    re-sort between the enumerate loop and the response return).

The FE merge layer keys on these ids (``message-merge.util.ts``
upsert-in-place keyed by ``message_id``) — an order drift between the
enumerate suffix and the emitted array silently corrupts merge
integrity. With a fixed context list and a bare last-user-turn
checkpoint, the block splices in at index 0
(``_locate_context_insertion_index``), so position-in-array ==
position-in-block == the enumerate suffix. A mid-array shape is pinned
alongside: the suffix sequence stays contiguous 0..N-1 in fixed-list
order (the no-re-sort invariant) even when the block lands before the
last user turn.

Only the external ``assemble_context_messages`` seam and the metadata
resolver are mocked — ``serialize_message``, the enumerate loop,
``_locate_context_insertion_index`` and the splice all run for real.

Run only this file::

    pytest tests/integration/test_persistence_synthetic_context_id_order.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from daemon.persistence import get_instance_messages

INSTANCE_ID = "inst-w9-order-1"

# Fixed context list — distinct kinds so order drift is observable.
FIXED_CONTEXT_KINDS = ["project", "shared_context", "skills", "project"]


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


def _fixed_context_list():
    """The FIXED context-message list the (mocked) assembler returns.

    Order here is the enumerate order the id suffixes are minted from.
    """
    persistent = [
        HumanMessage(
            content=f"[SYSTEM CONTEXT: {kind}]\n\n{kind} body #{i}",
            additional_kwargs={
                "injected_message": True,
                "context_kind": kind,
            },
        )
        for i, kind in enumerate(FIXED_CONTEXT_KINDS)
    ]
    return (persistent, [])


def _make_mock_checkpointer(messages):
    cp = MagicMock(name="Checkpointer")
    cp.aget = AsyncMock(return_value={
        "channel_values": {"messages": messages},
        "ts": "2026-09-08T00:00:00+00:00",
    })
    cp.alist = MagicMock(return_value=_EmptyAsyncIterator())
    return cp


async def _run_read_path(persisted_messages):
    """Drive the REAL ``get_instance_messages`` with a fixed context list."""
    instance_meta = SimpleNamespace(
        agent_id="developer",
        agent_tag=None,
        instance_metadata={},
        parent_id=None,
        project_id="project-w9",
        created_at="2026-09-08T00:00:00+00:00",
    )
    repo = MagicMock(name="InstanceRepository")
    repo.get = MagicMock(return_value=instance_meta)
    repo.set_metadata = MagicMock()
    manager = MagicMock(name="Manager")
    manager._instance_repository = repo
    manager._skill_repo = MagicMock()
    manager._skill_clone_service = None
    manager._project_repository = MagicMock()
    manager.shared_meta_kv_repo = MagicMock()
    manager.prompt_cache = MagicMock()
    manager.config = SimpleNamespace(llm=SimpleNamespace(allowed_models=[]))
    checkpointer = _make_mock_checkpointer(persisted_messages)
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
        new=AsyncMock(return_value=_fixed_context_list()),
    ), patch(
        "daemon.persistence._reconstruct_full_system_prompt",
        return_value=None,
    ):
        return await get_instance_messages(
            checkpointer, INSTANCE_ID, manager=manager
        )


def _context_entries(result):
    return [
        (i, d)
        for i, d in enumerate(result)
        if d.get("is_synthetic") and str(d.get("message_id", "")).startswith(
            "synthetic-context-"
        )
    ]


def _parse_synthetic_id(message_id: str) -> tuple[str, int]:
    """Parse ``synthetic-context-{kind}-{iid}-{idx}`` deterministically.

    The instance id itself contains hyphens, so a greedy kind regex
    would swallow it — split from the right: ``idx`` is the last token,
    and the kind is what remains after stripping the known prefix and
    the ``-{INSTANCE_ID}`` segment.
    """
    assert message_id.startswith("synthetic-context-"), message_id
    body = message_id[len("synthetic-context-"):]
    # rpartition → (head="kind-…-iid", sep="-", tail="idx")
    iid_and_kind, _, idx_str = body.rpartition("-")
    assert iid_and_kind.endswith(f"-{INSTANCE_ID}"), message_id
    kind = iid_and_kind[: -len(f"-{INSTANCE_ID}")]
    return kind, int(idx_str)


class TestSyntheticContextIdEnumerateOrder:
    """W9: enumerate suffix == position in the returned array."""

    @pytest.mark.asyncio
    async def test_idx_suffix_equals_array_position_block_at_top(self):
        """Bare single-turn checkpoint → the context block splices in at
        index 0, so each id suffix MUST equal the message's absolute
        position in the returned array, in fixed-list order."""
        persisted = [HumanMessage(content="the only user turn", id="msg-u1")]
        result = await _run_read_path(persisted)

        entries = _context_entries(result)
        assert len(entries) == len(FIXED_CONTEXT_KINDS), (
            "every fixed context message must surface as a synthetic entry"
        )
        # Block occupies the FRONT of the array, in fixed-list order.
        for array_pos, (_i, d) in enumerate(entries):
            assert array_pos == _i, (
                "the context block must be contiguous from index 0"
            )
            kind, idx = _parse_synthetic_id(d["message_id"])
            assert kind == FIXED_CONTEXT_KINDS[array_pos], (
                "array order must equal the fixed enumerate order "
                "(no re-sort between enumerate and return)"
            )
            assert idx == array_pos, (
                f"id suffix {idx} must equal the message's "
                f"position {array_pos} in the returned GET /messages array"
            )
        # The user turn follows the block.
        assert result[len(FIXED_CONTEXT_KINDS)]["role"] == "user"

    @pytest.mark.asyncio
    async def test_idx_suffix_sequence_contiguous_when_block_mid_array(self):
        """Two-turn checkpoint → the block splices before the LAST user
        turn. The suffixes stay contiguous 0..N-1 in fixed-list order —
        the no-re-sort invariant holds even when the block does not
        start the array."""
        persisted = [
            HumanMessage(content="first user turn", id="msg-u1"),
            HumanMessage(content="second user turn", id="msg-u2"),
        ]
        result = await _run_read_path(persisted)

        entries = _context_entries(result)
        assert len(entries) == len(FIXED_CONTEXT_KINDS)
        suffixes = []
        for block_pos, (_i, d) in enumerate(entries):
            kind, idx = _parse_synthetic_id(d["message_id"])
            assert kind == FIXED_CONTEXT_KINDS[block_pos], (
                "block order must equal the fixed enumerate order"
            )
            suffixes.append(idx)
        assert suffixes == list(range(len(FIXED_CONTEXT_KINDS))), (
            f"enumerate suffixes must stay contiguous and ordered "
            f"(no re-sort); got {suffixes}"
        )
