"""PR3 (C1) — frozen-fixture response-shape test (the binding contract).

Phase 1 C1 of the langgraph-checkpoint-perf plan (phase1-plan.md:519 —
``test_response_shape_byte_identical_to_captured_fixture``, Rev 2
BLOCKING). The fixture captured in PR1
(``tests/unit/persistence/fixtures/get_instance_messages_pre_phase1.json``,
extended pre-flip in 5d928d51 with the 2 synthetic-layer variants) is
THE contract this test enforces against the POST-C1 read path:

* Real ``AsyncSqliteSaver`` per variant + ``aupdate_state`` state
  injection (same harness as the capture test — no mock saver).
* A REAL ``MessageMetadataRepository`` (SQLite engine, temp DB) seeded
  with rows for the variant's known message ids, threaded through a
  manager stub exposing ``message_metadata_repo`` — so the flip's
  enrichment path (aget-only + side-table join) runs live against the
  frozen shape, not just the degradation path.
* The response is normalized with the SAME masking scheme as the
  capture (imported from the capture module — single source of truth)
  and compared against the fixture variant-by-variant: key sets, key
  ORDER, nesting (``tool_calls``), masked-value patterns.

Premise corrections pinned here (verified empirically, per the PR3
brief):

* The synthetic system message is UNREACHABLE on the true empty path —
  the ``state is None`` early-return fires BEFORE the synthetic block.
  The contract therefore carries TWO complementary variants:
  ``empty_history`` (response ``[]``) and ``synthetic_system``
  (armed manager + 1 persisted message → 11-key synthetic freeze).
  BOTH are asserted.
* Pre-normalization, tapped ids must show the SEEDED metadata
  timestamp (proving the enrichment source), untapped/id-less ids the
  ``state.ts`` fallback — the fallback chain in one test.

Marker gating: NO ``integration`` pytestmark — must execute under the
default ``addopts`` (see the GATE_SUITES.txt marker note).
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import aiosqlite
import pytest
from sqlalchemy import create_engine

# Single source of truth for the masking scheme + the synthetic stub —
# imported from the PR1 capture module so this test can never drift
# from the capture normalization.
from tests.integration.test_messages_response_fixture_capture import (
    _SYNTHETIC_STUB_PROMPT,
    VariantCapture,
    _normalize_message_for_fixture,
    _sanitize_variant_for_write,
)

FIXTURE_REL = Path(
    "tests/unit/persistence/fixtures/get_instance_messages_pre_phase1.json"
)

# Fixed metadata timestamps seeded per variant — distinct from any
# checkpoint ts so the enrichment-vs-fallback distinction is provable.
META_TS = "2026-08-26T00:00:00+00:00"

# Which message ids each variant seeds into message_metadata (the
# stable, deterministic ids the fixture sanitizer keeps literal).
_VARIANT_SEED_IDS: dict[str, list[str]] = {
    "id_less_human": [],          # id-less → no tap row possible (D19)
    "multimodal_human": ["v2-human-img"],
    "ai_tool_calls": ["v3-human", "v3-ai-toolcall"],
    "ai_thinking": ["v4-human", "v4-ai-think"],
    "empty_history": [],          # early-return; repo never consulted
    "synthetic_system": ["v6-human"],
}


@pytest.fixture(autouse=True)
def restore_langgraph_modules():
    """Undo the root conftest's mock of langgraph modules (see the
    capture test's identically-named fixture for the W10 rationale —
    snapshot/evict/restore leaves NO net sys.modules mutation)."""
    original_modules = {}
    mock_keys = [
        "langgraph",
        "langgraph.graph",
        "langgraph.graph.state",
        "langgraph.prebuilt",
        "langgraph.constants",
        "langgraph.checkpoint",
        "langgraph.checkpoint.sqlite",
        "langgraph.checkpoint.sqlite.aio",
    ]
    for key in mock_keys:
        if key in sys.modules:
            original_modules[key] = sys.modules[key]

    for key in mock_keys:
        if key in sys.modules:
            del sys.modules[key]

    daemon_modules_to_clear = [
        "daemon.compaction",
        "daemon.graph",
        "daemon.manager",
        "daemon.persistence",
    ]
    original_daemon_modules = {}
    for mod_name in daemon_modules_to_clear:
        if mod_name in sys.modules:
            original_daemon_modules[mod_name] = sys.modules[mod_name]

    for mod_name in daemon_modules_to_clear:
        sys.modules.pop(mod_name, None)

    yield

    for key in mock_keys:
        if key in original_modules:
            sys.modules[key] = original_modules[key]
        else:
            sys.modules.pop(key, None)

    for mod_name in daemon_modules_to_clear:
        if mod_name in original_daemon_modules:
            sys.modules[mod_name] = original_daemon_modules[mod_name]
        else:
            sys.modules.pop(mod_name, None)


async def _build_minimal_graph(checkpointer):
    """Minimum graph supporting ``aupdate_state`` (mirrors the capture)."""
    from langgraph.graph import END, START, MessagesState, StateGraph

    def noop(state):
        return {"messages": []}

    g = StateGraph(MessagesState)
    g.add_node("agent", noop)
    g.add_edge(START, "agent")
    g.add_edge("agent", END)
    return g.compile(checkpointer=checkpointer)


def _make_seeded_repo(db_file: Path) -> Any:
    """A REAL ``MessageMetadataRepository`` on a temp SQLite DB.

    The thread-specific rows are seeded by the caller per variant; the
    point of using the real repo (not a stub) is that the post-C1 read
    path exercises the actual SQL + ``get_for_thread`` mapping on the
    frozen-shape run.
    """
    from daemon.repositories.message_metadata.models import MessageMetadata
    from daemon.repositories.message_metadata.repository import (
        MessageMetadataRepository,
    )

    engine = create_engine(f"sqlite:///{db_file}")
    MessageMetadata.__table__.create(engine)
    return MessageMetadataRepository(engine)


async def _run_variant_post_c1(
    *,
    variant_id: str,
    thread_id: str,
    messages_to_inject: list[Any] | None,
    repo: Any,
    synthetic_system_prompt: str | None = None,
    tmp_db_path: Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run one variant through the POST-C1 ``get_instance_messages``.

    Returns ``(raw_response, normalized_response)``. The manager is a
    ``SimpleNamespace`` exposing ONLY ``message_metadata_repo`` — the
    synthetic layer is armed hermetically via the same helper patches
    the capture test uses (``_resolve_instance_message_context`` /
    ``_reconstruct_full_system_prompt``), so no live manager internals
    are ever touched. For the non-synthetic variants both helpers are
    patched to ``None``-returns so passing a manager (for the repo)
    does NOT activate the synthetic layer — keeping the persisted-only
    shape identical to the fixture's variants 1-4.
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    conn = await aiosqlite.connect(str(tmp_db_path))
    saver = AsyncSqliteSaver(conn)
    await saver.setup()

    try:
        graph = await _build_minimal_graph(saver)
        config = {"configurable": {"thread_id": thread_id}}

        if messages_to_inject is not None:
            await graph.aupdate_state(config, {"messages": messages_to_inject})

        manager = SimpleNamespace(message_metadata_repo=repo)

        from daemon.persistence import get_instance_messages

        if synthetic_system_prompt is not None:
            ctx_patch = patch(
                "daemon.persistence._resolve_instance_message_context",
                return_value=None,
            )
            recon_patch = patch(
                "daemon.persistence._reconstruct_full_system_prompt",
                return_value=(
                    synthetic_system_prompt,
                    datetime(2026, 8, 25, tzinfo=UTC),
                ),
            )
        else:
            # Disarm the synthetic layer entirely (manager is non-None
            # ONLY to carry the metadata repo).
            ctx_patch = patch(
                "daemon.persistence._resolve_instance_message_context",
                return_value=None,
            )
            recon_patch = patch(
                "daemon.persistence._reconstruct_full_system_prompt",
                return_value=None,
            )

        with ctx_patch, recon_patch:
            raw = await get_instance_messages(saver, thread_id, manager=manager)

        normalized = [_normalize_message_for_fixture(m) for m in raw]
        # Same sanitizer pass the capture writer applies (replaces the
        # id-less variant's generated UUID with the shape sentinel).
        normalized = _sanitize_variant_for_write(
            VariantCapture(variant_id=variant_id, messages=normalized)
        )["messages"]
        return raw, normalized
    finally:
        await conn.close()


# Variant payload restatement — same literals as the capture module's
# builders (the fixture equality assertion below IS the sync check: if
# either side drifts, the shape comparison fails).
async def _payloads() -> dict[str, tuple[str, list[Any] | None, str | None]]:
    from langchain_core.messages import AIMessage, HumanMessage

    return {
        "id_less_human": ("v1-idless", [HumanMessage(content="What time is it?")], None),
        "multimodal_human": (
            "v2-multimodal",
            [HumanMessage(
                content=[
                    {"type": "text", "text": "What is in this image?"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/test.png"}},
                ],
                id="v2-human-img",
            )],
            None,
        ),
        "ai_tool_calls": (
            "v3-toolcalls",
            [
                HumanMessage(content="Search for documents", id="v3-human"),
                AIMessage(
                    content="",
                    tool_calls=[{"id": "call_v3_1", "name": "search", "args": {"query": "documents"}}],
                    id="v3-ai-toolcall",
                ),
            ],
            None,
        ),
        "ai_thinking": (
            "v4-thinking",
            [
                HumanMessage(content="What is 2+2?", id="v4-human"),
                AIMessage(
                    content="The answer is 4.",
                    additional_kwargs={"reasoning_content": "Simple arithmetic: 2 plus 2 equals 4."},
                    id="v4-ai-think",
                ),
            ],
            None,
        ),
        "empty_history": ("v5-empty", None, _SYNTHETIC_STUB_PROMPT),
        "synthetic_system": (
            "v6-synth",
            [HumanMessage(content="Hello", id="v6-human")],
            _SYNTHETIC_STUB_PROMPT,
        ),
    }


@pytest.mark.asyncio
async def test_response_shape_frozen_fixture(tmp_path):
    """POST-C1 live serialization == the frozen 6-variant contract.

    For every variant: the normalized live response (same masking
    scheme as the capture) must equal the fixture's variant entry —
    key sets, key ORDER, nesting, masked-value patterns. Both the
    ``empty_history`` (``[]``) and ``synthetic_system`` (11-key set)
    variants are asserted explicitly.
    """
    fixture_path = Path(__file__).resolve().parents[2] / FIXTURE_REL
    assert fixture_path.exists(), (
        f"Fixture {FIXTURE_REL} is MISSING — it is the PR1-captured "
        "contract; produce it with REGENERATE_FIXTURE=1 on the PRE-C1 "
        "code path."
    )
    on_disk = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture_variants = {v["variant_id"]: v for v in on_disk["variants"]}
    assert set(fixture_variants) == set(_VARIANT_SEED_IDS)

    specs = await _payloads()

    raw_results: dict[str, list[dict[str, Any]]] = {}
    normalized_results: dict[str, list[dict[str, Any]]] = {}

    for variant_id, (thread_id, payload, synth) in specs.items():
        repo = _make_seeded_repo(tmp_path / f"{variant_id}-meta.db")
        seed_ids = _VARIANT_SEED_IDS[variant_id]
        if seed_ids:
            repo.upsert_batch(thread_id, [(mid, META_TS, i) for i, mid in enumerate(seed_ids)])

        raw, normalized = await _run_variant_post_c1(
            variant_id=variant_id,
            thread_id=thread_id,
            messages_to_inject=payload,
            repo=repo,
            synthetic_system_prompt=synth,
            tmp_db_path=tmp_path / f"{variant_id}.db",
        )
        raw_results[variant_id] = raw
        normalized_results[variant_id] = normalized

    # ── Per-variant contract: normalized live == fixture, key order too ──
    for variant_id, entry in fixture_variants.items():
        live = normalized_results[variant_id]
        fixture_msgs = entry["messages"]
        assert live == fixture_msgs, (
            f"FIXTURE SHAPE MISMATCH ({variant_id}): the POST-C1 read "
            f"path produced a different normalized shape than the frozen "
            f"contract.\nlive   : {json.dumps(live, indent=2)}\n"
            f"fixture: {json.dumps(fixture_msgs, indent=2)}"
        )
        # Key ORDER (dict == is order-insensitive; the frontend contract
        # includes ordering of the serialized keys).
        for live_msg, fixture_msg in zip(live, fixture_msgs):
            assert list(live_msg.keys()) == list(fixture_msg.keys()), (
                f"KEY ORDER MISMATCH ({variant_id}): "
                f"{list(live_msg.keys())} != {list(fixture_msg.keys())}"
            )

    # ── Premise correction 1: BOTH empty-path variants ──────────────────
    # empty_history → [] (the state-is-None early return fires BEFORE
    # the synthetic block — the synthetic message is UNREACHABLE here).
    assert raw_results["empty_history"] == [], (
        "EMPTY-path contract: no checkpoint → [] even with the synthetic "
        "layer armed (early return precedes synthetic injection)."
    )
    # synthetic_system → armed manager + 1 persisted message → the
    # synthetic system message at index 0 with the FULL 11-key set.
    synth_raw = raw_results["synthetic_system"]
    assert len(synth_raw) == 2
    synthetic_msg = synth_raw[0]
    assert synthetic_msg["message_id"] == "synthetic-system-v6-synth"
    assert set(synthetic_msg.keys()) == {
        "message_id", "type", "role", "content", "thinking",
        "thinking_extracted", "tool_calls", "images", "created_at",
        "instance_id", "is_synthetic",
    }
    assert synthetic_msg["is_synthetic"] is True
    assert "is_synthetic" not in synth_raw[1]

    # ── Masked-value pattern spot checks (the masking scheme holds) ─────
    idless = normalized_results["id_less_human"][0]
    assert idless["message_id"] == "<generated-uuid>"
    assert idless["content"].startswith("<str:")
    assert idless["created_at"] == "__TIMESTAMP_NORMALIZED__"
    mm = normalized_results["multimodal_human"][0]
    assert mm["images"] and mm["images"][0].startswith("<image_url:")
    tc = normalized_results["ai_tool_calls"][1]["tool_calls"]
    assert tc == [{
        "id": "call_v3_1", "name": "search",
        "arguments": {"query": "documents"}, "output": None,
    }]
    th = normalized_results["ai_thinking"][1]
    assert th["thinking"].startswith("<thinking:")

    # ── Fallback chain on the RAW (pre-normalization) responses ─────────
    # Tapped ids → the SEEDED metadata timestamp; untapped/id-less →
    # the state.ts fallback (the checkpoint ts aupdate_state wrote).
    v3_by_id = {m["message_id"]: m for m in raw_results["ai_tool_calls"]}
    assert v3_by_id["v3-human"]["created_at"] == META_TS
    assert v3_by_id["v3-ai-toolcall"]["created_at"] == META_TS
    # v1 id-less: no tap row possible → state.ts (non-null, != META_TS).
    idless_raw = raw_results["id_less_human"][0]
    assert idless_raw["created_at"]
    assert idless_raw["created_at"] != META_TS
    # v6: synthetic carries the fixed instance_created_at; the persisted
    # message carries the seeded metadata ts.
    assert synth_raw[0]["created_at"] == "2026-08-25T00:00:00+00:00"
    assert synth_raw[1]["created_at"] == META_TS


@pytest.mark.asyncio
async def test_frozen_fixture_no_alist_on_shape_run(tmp_path):
    """The shape run itself makes ZERO alist calls — a real-saver proof
    (complementing the mock-saver no-alist suite): alist is monkeypatched
    to FAIL LOUD if the flipped path ever reaches for it."""
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    conn = await aiosqlite.connect(str(tmp_path / "noalist.db"))
    saver = AsyncSqliteSaver(conn)
    await saver.setup()

    def _poison(*args, **kwargs):
        raise AssertionError(
            "POST-C1 INVARIANT VIOLATION: saver.alist was invoked on the "
            "get_instance_messages path (the read flip must be aget-only)."
        )

    saver.alist = _poison  # type: ignore[method-assign]
    try:
        graph = await _build_minimal_graph(saver)
        await graph.aupdate_state(
            {"configurable": {"thread_id": "thr-noalist"}},
            {"messages": [HumanMessage(content="hi", id="m-1")]},
        )

        from daemon.persistence import get_instance_messages

        repo = _make_seeded_repo(tmp_path / "noalist-meta.db")
        repo.upsert_batch("thr-noalist", [("m-1", META_TS, 0)])
        manager = SimpleNamespace(message_metadata_repo=repo)

        out = await get_instance_messages(saver, "thr-noalist", manager=manager)
        assert len(out) == 1
        assert out[0]["created_at"] == META_TS
    finally:
        await conn.close()
