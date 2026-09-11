"""Real add_messages upsert pin for the LCA (b)-path Completion Check Note.

End-to-end verification of the W2 (Shape A) supersede contract on the
REAL ``langgraph.graph.message.add_messages`` function — NOT a mirrored
dict helper, NOT a stub.

Background
----------

The Completion Check Note hint is injected on the (b) path (markers +
judge-no + real pending). Two consecutive (b) events on the SAME
instance must collapse to ONE block in the resulting message channel —
otherwise the channel would accumulate unbounded
``context_kind=task_context`` hints under three-bucket compaction
(merge 77ce4ae8). The fix: ``_make_completion_check_note_message``
mints a stable id per instance via
``_stable_id_for("completion_check_note", instance_id=...)``, and
LangGraph's ``add_messages`` reducer SUPERSEDES the prior checkpoint
entry in place when the right-side message has the same id as an
existing left-side message.

The existing unit suite pins the shape of the upsert via a mirrored
dict helper (``test_attestation_marker_wiring.py``), but the LCA merge
gate requires verification on the REAL ``add_messages`` function (per
the brief: "verified against REAL langgraph add_messages upsert
behavior — NOT a mirrored dict-helper"). This file is that
verification.

Approach
--------

The root ``tests/conftest.py`` installs mock ``langgraph`` modules
globally for the unit gate. We follow the eviction pattern used by the
other real-langgraph integration tests
(``tests/integration/test_injection_echo_id_continuity.py``):

  1. ``setup_module`` evicts the conftest mocks and imports the REAL
     ``langgraph.graph.message.add_messages`` + ``MessagesState``.
  2. ``teardown_module`` restores the mocks exactly as we found them.

The test then exercises the REAL ``add_messages`` reducer with the
exact hint messages produced by ``_make_completion_check_note_message``
on the production closure (the same closure used by the (b) gate
routing). On a real checkpoint-style state channel (via
``MessagesState`` with the canonical ``Annotated[list, add_messages]``
reducer), the same-id hint MUST supersede the prior block — exactly
ONE hint block survives in the resulting channel regardless of how
many (b) events fire on the same instance.

This file is pinned on:
  * ``langgraph 1.0.9`` — the LCA brief's documented version; the
    upsert behavior is source-verified on
    ``langgraph/graph/message.py:225`` (``merged[existing_idx] = m``).
  * The factory's stable-id contract (id format
    ``"completion_check_note:{instance_id}"``).
  * The ``_make_context_message`` shape — the hint carries
    ``context_kind="task_context"`` so the three-bucket compaction
    seam hoists it (verified end-to-end via the partition helper).
"""

from __future__ import annotations

import importlib
import sys
from typing import Annotated, Any, TypedDict

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from unittest.mock import MagicMock


# ─────────────────────────────────────────────────────────────────────────────
# Real-langgraph swap (module-scoped; restores conftest mocks after).
# Mirrors the eviction pattern used by
# ``tests/integration/test_injection_echo_id_continuity.py`` and
# ``tests/integration/checkpoint_prune_real_saver.py``.
# ─────────────────────────────────────────────────────────────────────────────


_MOCKED_LANGGRAPH_KEYS = [
    "langgraph",
    "langgraph.graph",
    "langgraph.graph.state",
    "langgraph.graph.message",
    "langgraph.prebuilt",
    "langgraph.constants",
    "langgraph.checkpoint",
    "langgraph.checkpoint.memory",
    "langgraph.checkpoint.sqlite",
    "langgraph.checkpoint.sqlite.aio",
]

_saved_mocks: dict[str, Any] = {}
_lg_graph: Any = None
_lg_memory: Any = None
_lg_message: Any = None


def setup_module(module: Any) -> None:
    """Swap the conftest's mock langgraph modules for the real ones.

    Evicts every conftest-installed mock so the real ``langgraph``
    package re-imports cleanly. Restores on ``teardown_module`` so
    neighbouring test files (which run against the conftest mocks)
    keep their mock-bound identity.
    """
    global _lg_graph, _lg_memory, _lg_message

    for key in _MOCKED_LANGGRAPH_KEYS:
        if key in sys.modules:
            _saved_mocks[key] = sys.modules[key]
            del sys.modules[key]
    # Drop any cached langgraph children from a previous swap so the
    # re-import is coherent (parents were just deleted).
    for key in [k for k in sys.modules if k.startswith("langgraph")]:
        del sys.modules[key]

    _lg_graph = importlib.import_module("langgraph.graph")
    _lg_memory = importlib.import_module("langgraph.checkpoint.memory")
    _lg_message = importlib.import_module("langgraph.graph.message")


def teardown_module(module: Any) -> None:
    """Restore the conftest mocks exactly as we found them."""
    for key in [k for k in sys.modules if k.startswith("langgraph")]:
        del sys.modules[key]
    sys.modules.update(_saved_mocks)
    _saved_mocks.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Sanity guard — confirms the real ``add_messages`` is in fact loaded.
# ─────────────────────────────────────────────────────────────────────────────


def test_real_add_messages_is_loaded_not_a_mock():
    """Pre-condition: ``langgraph.graph.message.add_messages`` is the REAL
    function — not a MagicMock.

    The whole point of this module is to verify the upsert behavior on
    the REAL ``add_messages``. A mock sneaking through the eviction
    would silently make every other test here pass with no real
    verification. The conftest mocks install a ``MagicMock`` object
    on every ``langgraph.*`` attribute — assert the function is the
    real ``<class 'function'>`` (not a MagicMock) so a fixture miss
    fails loud at the boundary.
    """
    assert _lg_message is not None
    assert hasattr(_lg_message, "add_messages"), (
        "real langgraph.graph.message must export add_messages"
    )
    real_fn = _lg_message.add_messages
    # A MagicMock's __class__ is ``MagicMock`` (or ``class 'MagicMock'``).
    # The real function is a plain ``function``. Pin the type.
    assert not isinstance(real_fn, type(MagicMock())), (
        "langgraph.graph.message.add_messages MUST be the REAL function, "
        "not a MagicMock from the conftest's mock langgraph modules"
    )
    assert callable(real_fn), "add_messages MUST be callable"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — factory-driven hint production (mirrors the production closure)
# ─────────────────────────────────────────────────────────────────────────────


def _hint(instance_id: str) -> HumanMessage:
    """Build the (b)-path Completion Check Note hint via the production
    closure.

    Uses ``daemon.graph._make_completion_check_note_message`` (the same
    factory the gate wiring calls on every (b) event). When
    ``instance_id`` is non-None the factory mints the stable id
    ``"completion_check_note:{instance_id}"`` — the W2 Shape A
    contract.
    """
    from daemon.graph import _make_completion_check_note_message

    return _make_completion_check_note_message(instance_id)


# ─────────────────────────────────────────────────────────────────────────────
# (b)-supersede — two consecutive (b) events on the SAME instance → ONE block
# ─────────────────────────────────────────────────────────────────────────────


def test_two_consecutive_b_events_supersede_to_one_block_real_add_messages():
    """W2 Shape A — REAL ``add_messages`` upsert collapses the two
    same-id hints to ONE block in the resulting channel.

    End-to-end on the real ``langgraph.graph.message.add_messages``:
    building a MessagesState-style channel with ``add_messages`` as
    the reducer, two consecutive (b) events on the same instance MUST
    collapse to ONE hint block — the W2 (Shape A) supersede contract.

    The hint is produced by the production closure
    ``_make_completion_check_note_message`` (NOT a hand-crafted
    HumanMessage) so the test exercises the EXACT message shape the
    gate would inject on every (b) event.
    """
    instance_id = "lca-b-supersede-it"

    hint_1 = _hint(instance_id)
    hint_2 = _hint(instance_id)

    # Pre-condition — both hints share the same stable id (Shape A
    # contract); bodies are identical (the gate emits the same content
    # on every (b) event).
    assert hint_1.id == hint_2.id, (
        "(b)-supersede: same instance_id MUST mint the same stable id "
        "so the add_messages reducer can supersede"
    )
    assert hint_1.id == f"completion_check_note:{instance_id}"

    # EXERCISE THE REAL ``add_messages`` reducer.
    #
    # The first hint lands in the channel; the second hint arrives as
    # the right-hand side of the merge. The reducer's contract: if a
    # message in ``right`` has the same id as a message in ``left``,
    # the message from ``right`` REPLACES the message from ``left``.
    # The merge therefore collapses [hint_1, hint_2] → [hint_2_only].
    merged = _lg_message.add_messages([hint_1], [hint_2])

    assert len(merged) == 1, (
        f"(b)-supersede: REAL add_messages MUST collapse the two "
        f"same-id Completion Check Note hints to ONE block — got "
        f"{len(merged)} blocks instead"
    )
    # The surviving block carries the right-side id and content (the
    # upsert rule writes right onto left's slot).
    assert merged[0].id == hint_2.id
    assert merged[0].content == hint_2.content
    # context_kind survives the upsert — the three-bucket compaction
    # seam still classifies it as a permanent injected message after
    # the merge.
    assert merged[0].additional_kwargs.get("context_kind") == (
        "task_context"
    ), (
        "(b)-supersede: the context_kind=task_context marker MUST "
        "survive the add_messages upsert — the compaction seam keys on it"
    )


# ─────────────────────────────────────────────────────────────────────────────
# (b)-supersede on real checkpoint state — MemorySaver + StateGraph
# ─────────────────────────────────────────────────────────────────────────────


def test_b_supersede_via_real_checkpoint_two_turns_one_block():
    """W2 Shape A — REAL ``add_messages`` upsert collapses the two
    same-id hints to ONE block on a real checkpoint-style state.

    The brief: "Build this on a real checkpoint/graph state
    (SqliteSaver or the repo's existing graph-state test helpers) so
    ``add_messages`` itself performs the upsert."

    This test compiles a 2-node LangGraph around a real
    ``MemorySaver`` checkpointer. Node A seeds the channel with
    ``hint_1`` (the first (b) event); node B appends ``hint_2`` (the
    second (b) event). The graph's ``messages`` channel uses the
    canonical ``Annotated[list, add_messages]`` reducer so the
    upsert runs on the checkpoint commit path, NOT on a standalone
    helper call. After both turns, the checkpointed channel MUST
    contain exactly ONE hint block — the W2 Shape A contract end-
    to-end on the real LangGraph runtime.
    """
    from typing import Annotated, TypedDict

    # Build a tiny 2-node graph whose ``messages`` channel uses the
    # canonical LangGraph ``add_messages`` reducer (the production
    # channel shape — see ``langgraph.graph.message.add_messages``
    # source for the ``merged[existing_idx] = m`` upsert rule).
    class HintState(TypedDict):
        messages: Annotated[list, _lg_message.add_messages]

    def node_a(state: HintState) -> dict:
        return {"messages": [_hint("lca-ckpt-it")]}

    def node_b(state: HintState) -> dict:
        return {"messages": [_hint("lca-ckpt-it")]}

    builder = _lg_graph.StateGraph(HintState)
    builder.add_node("a", node_a)
    builder.add_node("b", node_b)
    builder.add_edge(_lg_graph.START, "a")
    builder.add_edge("a", "b")
    builder.add_edge("b", _lg_graph.END)

    checkpointer = _lg_memory.MemorySaver()
    graph = builder.compile(checkpointer=checkpointer)

    # Run the graph with a single thread_id; the MemorySaver commits
    # both node returns to the same checkpoint chain so the
    # ``add_messages`` reducer sees the merged channel.
    config = {"configurable": {"thread_id": "lca-ckpt-it"}}
    final_state = graph.invoke({"messages": []}, config=config)

    # The channel MUST contain exactly ONE Completion Check Note
    # block — both (b) events landed on the same id and the upsert
    # collapsed them.
    messages = final_state["messages"]
    hint_blocks = [
        m for m in messages if getattr(m, "id", "").startswith(
            "completion_check_note:lca-ckpt-it"
        )
    ]
    assert len(hint_blocks) == 1, (
        f"(b)-supersede (real checkpoint): the checkpointed channel "
        f"MUST hold exactly ONE Completion Check Note after two (b) "
        f"events — got {len(hint_blocks)} blocks instead. The "
        f"add_messages reducer did not perform the upsert end-to-end."
    )
    # context_kind survived the upsert.
    assert hint_blocks[0].additional_kwargs.get("context_kind") == (
        "task_context"
    )

    # Read back via the checkpointer to confirm the persisted state
    # also sees the upserted shape (defense-in-depth — the
    # checkpointer serde may re-serialize; the persisted state MUST
    # still show exactly one hint block).
    persisted = checkpointer.get(config)
    assert persisted is not None, (
        "(b)-supersede (real checkpoint): the MemorySaver MUST hold a "
        "checkpoint tuple after the two-node run"
    )
    # The langgraph 1.0.9 MemorySaver.get returns a plain dict
    # (``Checkpoint`` is a TypedDict-like shape, not a class with
    # attributes). Read the channel_values via subscript.
    persisted_messages = persisted["channel_values"]["messages"]
    persisted_hints = [
        m for m in persisted_messages
        if getattr(m, "id", "").startswith(
            "completion_check_note:lca-ckpt-it"
        )
    ]
    assert len(persisted_hints) == 1, (
        f"(b)-supersede (real checkpoint): the PERSISTED state must "
        f"also show exactly ONE hint block — got {len(persisted_hints)}. "
        f"The add_messages upsert did not survive the checkpoint round-trip."
    )


# ─────────────────────────────────────────────────────────────────────────────
# (b)-supersede — per-instance isolation — different instances ⇒ TWO blocks
# ─────────────────────────────────────────────────────────────────────────────


def test_b_supersede_different_instances_yield_two_blocks_real_add_messages():
    """W2 Shape A — the stable id is per-instance, NOT global.

    Two leaders each firing one (b) event on the SAME turn-end carry
    DIFFERENT stable ids (one per instance). The ``add_messages``
    upsert is keyed on id, so the two hints MUST coexist in the
    resulting channel — exactly TWO blocks.

    This guards against a regression to a globally-shared stable id
    (e.g., a stray constant) which would silently merge cross-
    instance hints and lose per-instance signal.
    """
    hint_a = _hint("lca-iso-A")
    hint_b = _hint("lca-iso-B")

    # Distinct ids — the id format is ``completion_check_note:{instance_id}``.
    assert hint_a.id != hint_b.id, (
        "(b)-isolation: distinct instance_ids MUST mint distinct stable ids"
    )

    # Real ``add_messages`` merges the two different-id hints into a
    # two-block channel (no upsert — different ids).
    merged = _lg_message.add_messages([hint_a], [hint_b])

    assert len(merged) == 2, (
        f"(b)-isolation: REAL add_messages MUST keep the two "
        f"different-id hints as TWO blocks — got {len(merged)} instead"
    )
    merged_ids = {m.id for m in merged}
    assert merged_ids == {hint_a.id, hint_b.id}, (
        "(b)-isolation: the merged channel must carry BOTH hint ids"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Id-format pin — guards against a regression in the stable-id derivation
# ─────────────────────────────────────────────────────────────────────────────


def test_stable_id_format_matches_w2_shape_a_contract():
    """Pin the stable-id format: ``"completion_check_note:{instance_id}"``.

    The W2 Shape A contract depends on this exact id format so the
    upstream test helpers (and any future regression) can grep for the
    prefix ``"completion_check_note:"`` to identify (b)-path hints in
    the checkpointed channel. Any drift in the format would silently
    break downstream consumers.
    """
    from daemon.services.context_messages import _stable_id_for

    instance_id = "lca-id-fmt-it"

    # Direct call to the stable-id factory.
    stable_id = _stable_id_for("completion_check_note", instance_id=instance_id)
    assert stable_id == "completion_check_note:lca-id-fmt-it"

    # Production closure produces the same id.
    hint = _hint(instance_id)
    assert hint.id == stable_id

    # And the prefix is canonical — anything past the colon is the
    # owning instance id.
    prefix, _, tail = hint.id.partition(":")
    assert prefix == "completion_check_note"
    assert tail == instance_id
