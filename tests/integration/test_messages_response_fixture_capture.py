"""Fixture freeze for PR1 (C4) — the pre-C1 GET /messages response shape.

Spec — ``.agents/shared/planning/langgraph-checkpoint-perf/phase1-plan.md``
lines 95 + 207 (Rollback procedure 2): the fixture file is the contract
that PR3 matches byte-for-byte.

Contract (post W1/W11 rework)
----------------------------

* The on-disk fixture is a COMMITTED contract artifact, not a test
  byproduct. The test captures fresh, in-memory, every run.
* ``REGENERATE_FIXTURE=1`` in the environment is the ONLY path that
  writes the file — a deliberate regeneration used when the response
  shape genuinely changes (a breaking event; say so in the PR).
* Without the env var the test loads the on-disk fixture and asserts
  FRESH-CAPTURE == ON-DISK (structural). Drift fails the test; a
  MISSING file fails the test (never skips — S1). The package-version
  header (``_meta.packages``) is compared too, so a fixture captured
  under different langgraph/langchain versions fails loud (S3).
* Reproducibility is asserted for ALL 4 variants (double capture,
  structural equality), not just one (W1).

Provenance
----------

* **Harness**: real ``AsyncSqliteSaver`` on a temp DB (one per variant),
  so the ``alist()`` walk runs against a real LangGraph schema with
  real checkpoint tuples. No mock saver, no fake iterator — this is
  what production hits.
* **Code path under test**: the CURRENT (pre-C1) ``get_instance_messages``
  on the raw saver (the read path GET /messages delegates to via
  ``InstanceMessagingService.get_messages`` → ``daemon.persistence``).
  ``manager=None`` so no synthetic system prompt is injected — the
  captured shape is the PERSISTED shape only (the synthetic layer is
  additive and already covered by tests/integration/test_api_messages.py).
* **State injection**: ``graph.aupdate_state(config, {"messages": [...]})``
  per variant. No real LLM — the test injects checkpoint state directly,
  which avoids LLM nondeterminism while still exercising the real saver.
* **Variants** (4, per plan line 95):
    1. id-less HumanMessage
    2. multimodal HumanMessage with image content blocks
    3. AIMessage with tool_calls
    4. AIMessage with thinking/reasoning_content
* **Determinism normalization** (per plan risk table line 200):
  capture only message METADATA: ids, role, content-types, tool_call
  structure, message order. ``created_at`` is replaced with a sentinel
  (``"__TIMESTAMP_NORMALIZED__"``) because LangGraph ``ts`` is real-time
  wall-clock at write-time; free-text content is replaced by a typed
  length marker (``"<str:N>"``). The equality assertions are
  field-level/structural, never byte/text equality.
* **Repro**: the test captures every variant TWICE per run and asserts
  the two in-memory captures are structurally equal, then compares the
  first capture against the on-disk fixture.
* **File schema** (v2): ``{"_meta": {…provenance + package versions…},
  "variants": [ …the 4 variant entries… ]}``.
* **Captured**: 2026-08-25, branch ``feature/langgraph-checkpoint-perf``
  (PR1 working tree), pre-C1 code path (alist walk present).

ALSO captures and prints the observed ``alist_count`` per variant to the
test log — this is the pre-C1 baseline the dispatcher reports upward.

NOTE on lazy imports: the root ``tests/conftest.py`` replaces
``langgraph.*`` with MagicMocks for unit tests. This module therefore
imports ALL langgraph symbols lazily inside functions — the
``restore_langgraph_modules`` fixture (autouse) has already restored the
real modules by the time any helper executes. The same fixture also
snapshots/restores the four evicted ``daemon.*`` modules so the test
leaves NO net ``sys.modules`` mutation (W10).
"""
from __future__ import annotations

import importlib.metadata
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Restore the real langgraph modules (root conftest mocks them for unit tests)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def restore_langgraph_modules():
    """Undo the root conftest's mock of langgraph modules.

    The root conftest at ``tests/conftest.py`` replaces
    ``langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver`` with a MagicMock
    so unit tests can stub the saver cheaply. This integration test
    needs the REAL saver (real DB, real alist walk).

    W10 — cross-test pollution fix: the four ``daemon.*`` modules below
    are evicted so they re-import against the REAL langgraph modules.
    They are snapshotted first and restored symmetrically (the same
    pattern as ``mock_keys``), so this test leaves NO net
    ``sys.modules`` mutation: without the restore, later unit tests in
    the same process would see a ``daemon.persistence`` bound to the
    real langgraph instead of the conftest mocks.
    """
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

    # W10 — snapshot BEFORE evicting, restore AFTER the test (below).
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

    # W10 — restore the pre-test daemon.* bindings (or remove the ones
    # this test imported, if they were absent pre-test). Net sys.modules
    # delta from this fixture: zero.
    for mod_name in daemon_modules_to_clear:
        if mod_name in original_daemon_modules:
            sys.modules[mod_name] = original_daemon_modules[mod_name]
        else:
            sys.modules.pop(mod_name, None)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


FIXTURE_REL = Path(
    "tests/unit/persistence/fixtures/get_instance_messages_pre_phase1.json"
)


@dataclass
class VariantCapture:
    """One captured variant: normalized messages + observed alist_count."""

    variant_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    alist_count: int = 0
    bytes_estimate: int = 0


def _normalize_message_for_fixture(msg: dict[str, Any]) -> dict[str, Any]:
    """Strip non-deterministic fields + tag with shape-only metadata.

    Per plan risk table (line 200): capture METADATA, not free-text
    content. We keep ``content`` only as a type marker (``"<str:N>"``) so
    the shape test still catches accidental shape changes without
    coupling to LLM output text. ``created_at`` is replaced with a
    sentinel for the same reason.
    """
    out: dict[str, Any] = {}
    for key, val in msg.items():
        if key == "created_at":
            # ts is wall-clock at write-time → not deterministic across runs
            out[key] = "__TIMESTAMP_NORMALIZED__"
        elif key == "content":
            # Keep content type, length, and structural markers; drop free text
            if isinstance(val, str):
                out[key] = f"<str:{len(val)}>"
            elif isinstance(val, list):
                # Multimodal: list of content blocks (text/image/etc.)
                kinds = [
                    b.get("type") if isinstance(b, dict) else type(b).__name__
                    for b in val
                ]
                out[key] = f"<list:{kinds}>"
            else:
                out[key] = f"<{type(val).__name__}>"
        elif key == "images":
            # Image URLs are data — keep the shape, redact the URL itself
            if val is None:
                out[key] = None
            else:
                out[key] = [f"<image_url:{len(u)}>" for u in val]
        elif key == "thinking":
            # Thinking is LLM-generated prose — keep only the type/length
            if isinstance(val, str):
                out[key] = f"<thinking:{len(val)}>"
            else:
                out[key] = val
        elif key == "thinking_extracted":
            if isinstance(val, str):
                out[key] = f"<think_extracted:{len(val)}>"
            else:
                out[key] = val
        else:
            out[key] = val
    return out


def _get_real_async_sqlite_saver():
    """Get the real AsyncSqliteSaver class (mirrors test_compaction_e2e.py)."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    return AsyncSqliteSaver


async def _build_minimal_graph(checkpointer):
    """Build the minimum graph that supports ``aupdate_state``.

    The test does NOT need a real LLM. We just need a graph that:
    - has a messages-capable state schema the saver accepts
    - supports ``aupdate_state(config, {"messages": [...]})``

    A trivial pass-through graph with one node suffices. Imports are
    lazy so the real (restored) langgraph modules are used.
    """
    from langgraph.graph import END, START, MessagesState, StateGraph

    def noop(state):
        return {"messages": []}

    g = StateGraph(MessagesState)
    g.add_node("agent", noop)
    g.add_edge(START, "agent")
    g.add_edge("agent", END)
    return g.compile(checkpointer=checkpointer)


async def _capture_variant(
    *,
    variant_id: str,
    thread_id: str,
    messages_to_inject: list[Any],
    tmp_db_path: Path,
    caplog,
) -> VariantCapture:
    """Run one variant and capture (normalized response, alist_count)."""
    AsyncSqliteSaver = _get_real_async_sqlite_saver()

    # Real saver on a temp DB — guarantees a real alist walk.
    conn = await aiosqlite.connect(str(tmp_db_path))
    saver = AsyncSqliteSaver(conn)
    await saver.setup()

    try:
        graph = await _build_minimal_graph(saver)
        config = {"configurable": {"thread_id": thread_id}}

        # Inject state — creates checkpoint tuples via the real saver.
        await graph.aupdate_state(
            config,
            {"messages": messages_to_inject},
        )

        # PR1 (C4) observation: the pre-C1 code path still walks alist.
        # get_instance_messages is imported lazily (post-restore) so it
        # binds to the REAL saver interface.
        from daemon.persistence import get_instance_messages

        caplog.clear()
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            messages = await get_instance_messages(saver, thread_id, manager=None)

        # Find the [/Messages] line and parse alist_count + bytes from it.
        alist_count = 0
        bytes_estimate = 0
        for rec in caplog.records:
            if "[/Messages]" in rec.message:
                for tok in rec.message.split():
                    if tok.startswith("alist_count="):
                        alist_count = int(tok.split("=", 1)[1])
                    elif tok.startswith("bytes="):
                        bytes_estimate = int(tok.split("=", 1)[1])

        normalized = [_normalize_message_for_fixture(m) for m in messages]
        return VariantCapture(
            variant_id=variant_id,
            messages=normalized,
            alist_count=alist_count,
            bytes_estimate=bytes_estimate,
        )
    finally:
        await conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Fixture variants
# ─────────────────────────────────────────────────────────────────────────────


async def _variant_id_less(tmp_db_path: Path, caplog):
    """Variant 1: HumanMessage with NO ``id``.

    Per plan decisions.md D19 + Out-of-Scope: id-less messages fall to the
    ``state.ts`` timestamp fallback, and serialization generates a fresh
    UUID for ``message_id`` (serialize_message fallback). The fixture
    therefore normalizes the generated id to a stable sentinel (handled
    in the writer below — the UUID itself is not deterministic).
    """
    from langchain_core.messages import HumanMessage

    return await _capture_variant(
        variant_id="id_less_human",
        thread_id="v1-idless",
        messages_to_inject=[
            HumanMessage(content="What time is it?"),
        ],
        tmp_db_path=tmp_db_path,
        caplog=caplog,
    )


async def _variant_multimodal(tmp_db_path: Path, caplog):
    """Variant 2: multimodal HumanMessage with image content blocks.

    Tests that image content survives the round-trip through aget + alist
    + serialize_message. The URL itself is redacted in the fixture.
    """
    from langchain_core.messages import HumanMessage

    return await _capture_variant(
        variant_id="multimodal_human",
        thread_id="v2-multimodal",
        messages_to_inject=[
            HumanMessage(
                content=[
                    {"type": "text", "text": "What is in this image?"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/test.png"},
                    },
                ],
                id="v2-human-img",
            ),
        ],
        tmp_db_path=tmp_db_path,
        caplog=caplog,
    )


async def _variant_ai_tool_calls(tmp_db_path: Path, caplog):
    """Variant 3: AIMessage with ``tool_calls`` — captures tool_call shape.

    The tool_call args dict is kept (it's deterministic) — tool function
    arguments are fully under the test's control, not LLM free-text.
    """
    from langchain_core.messages import AIMessage, HumanMessage

    return await _capture_variant(
        variant_id="ai_tool_calls",
        thread_id="v3-toolcalls",
        messages_to_inject=[
            HumanMessage(content="Search for documents", id="v3-human"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call_v3_1",
                        "name": "search",
                        "args": {"query": "documents"},
                    },
                ],
                id="v3-ai-toolcall",
            ),
        ],
        tmp_db_path=tmp_db_path,
        caplog=caplog,
    )


async def _variant_ai_thinking(tmp_db_path: Path, caplog):
    """Variant 4: AIMessage with thinking/reasoning_content.

    Captures how ``serialize_message`` lifts ``additional_kwargs['thinking']``
    (or ``reasoning_content``) into the response. Per daemon/utils.py
    five extraction paths exist; this variant exercises the
    ``reasoning_content`` path.
    """
    from langchain_core.messages import AIMessage, HumanMessage

    return await _capture_variant(
        variant_id="ai_thinking",
        thread_id="v4-thinking",
        messages_to_inject=[
            HumanMessage(content="What is 2+2?", id="v4-human"),
            AIMessage(
                content="The answer is 4.",
                additional_kwargs={
                    "reasoning_content": "Simple arithmetic: 2 plus 2 equals 4.",
                },
                id="v4-ai-think",
            ),
        ],
        tmp_db_path=tmp_db_path,
        caplog=caplog,
    )


# ─────────────────────────────────────────────────────────────────────────────
# The test
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def fixture_path():
    """Canonical fixture path under the repo (same file PR3 consumes)."""
    repo_root = Path(__file__).resolve().parents[2]
    canonical = repo_root / FIXTURE_REL
    canonical.parent.mkdir(parents=True, exist_ok=True)
    return canonical


def _sanitize_variant_for_write(cap: VariantCapture) -> dict[str, Any]:
    """Post-normalize the id-less variant's generated UUID message_id.

    ``serialize_message`` generates a fresh UUID for id-less messages —
    NOT deterministic across runs. Replace any message_id that isn't one
    of the stable ids we injected with a shape sentinel so re-runs are
    structurally identical.
    """
    stable_ids = {
        "v2-human-img",
        "v3-human",
        "v3-ai-toolcall",
        "v4-human",
        "v4-ai-think",
    }
    out_messages = []
    for m in cap.messages:
        m = dict(m)
        mid = m.get("message_id")
        if isinstance(mid, str) and mid not in stable_ids:
            m["message_id"] = "<generated-uuid>"
        out_messages.append(m)
    return {
        "variant_id": cap.variant_id,
        "messages": out_messages,
        "observed_alist_count": cap.alist_count,
        "bytes_estimate": cap.bytes_estimate,
    }


def _package_version(name: str) -> str:
    """Installed distribution version, or the literal ``not-installed``.

    S3 — recorded in the fixture ``_meta`` header so a fixture produced
    under different library versions fails the drift check loud. In this
    repo ``langchain`` itself is typically absent (only ``langchain-core``
    is installed) — the marker documents that honestly instead of
    raising.
    """
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def _fixture_meta() -> dict[str, Any]:
    """Provenance header written alongside the variants (S3).

    ``captured`` is the regeneration date (not compared on drift — it
    naturally differs); ``packages`` IS compared so environment drift
    (langgraph/langchain upgrade) fails the test until the fixture is
    deliberately regenerated.
    """
    return {
        "schema": "get_instance_messages_pre_phase1:v2",
        "captured": datetime.now(timezone.utc).date().isoformat(),
        "branch": "feature/langgraph-checkpoint-perf",
        "code_path": "pre-C1 (alist walk present) — daemon/persistence.py::get_instance_messages",
        "packages": {
            "langgraph": _package_version("langgraph"),
            "langgraph-checkpoint": _package_version("langgraph-checkpoint"),
            "langchain": _package_version("langchain"),
            "langchain-core": _package_version("langchain-core"),
        },
        "regeneration": (
            "REGENERATE_FIXTURE=1 uv run pytest "
            "tests/integration/test_messages_response_fixture_capture.py"
        ),
    }


async def _capture_all_variants(
    db_dir: Path, caplog, db_suffix: str = ""
) -> list[dict[str, Any]]:
    """Run all 4 variants on fresh DBs → sanitized fixture entries."""
    caps = []
    for variant_fn, db_stem in [
        (_variant_id_less, "v1"),
        (_variant_multimodal, "v2"),
        (_variant_ai_tool_calls, "v3"),
        (_variant_ai_thinking, "v4"),
    ]:
        caplog.clear()
        cap = await variant_fn(db_dir / f"{db_stem}{db_suffix}.db", caplog)
        caps.append(cap)
    return [_sanitize_variant_for_write(cap) for cap in caps]


@pytest.mark.asyncio
async def test_messages_response_fixture_capture(fixture_path, tmp_path, caplog):
    """Capture fresh → (regenerate | drift-check) → reproducibility (W1/W11).

    1. Capture all 4 variants fresh, in-memory (real AsyncSqliteSaver).
    2. Reproducibility: capture ALL 4 a second time (fresh DBs) and
       assert the normalized shapes are structurally equal — not just
       one representative variant as before.
    3. Disk contract:
       - ``REGENERATE_FIXTURE=1`` → write the fixture (the ONLY
         deliberate write path).
       - otherwise → the on-disk fixture MUST exist (missing = FAIL,
         never skip — S1) and MUST equal the fresh capture (drift =
         FAIL). The package-version header is compared too (S3).
    """
    # Use temp dirs per variant — each variant gets a fresh DB so checkpoints
    # don't bleed across variants.
    db_dir = tmp_path / "dbs"
    db_dir.mkdir(parents=True, exist_ok=True)

    # ── (1) Fresh capture, all 4 variants ──────────────────────────────────
    fixture_variants = await _capture_all_variants(db_dir, caplog)

    # ── (2) Reproducibility: double capture, structural equality ──────────
    re_run_variants = await _capture_all_variants(db_dir, caplog, db_suffix="-redo")
    assert re_run_variants == fixture_variants, (
        "Capture is NOT reproducible — the SAME code path produced "
        "structurally different normalized shapes across two in-process "
        f"runs.\nfirst : {json.dumps(fixture_variants, indent=2)}\n"
        f"second: {json.dumps(re_run_variants, indent=2)}"
    )

    # ── (3) Disk contract ──────────────────────────────────────────────────
    if os.environ.get("REGENERATE_FIXTURE") == "1":
        # Deliberate regeneration path — the ONLY write. Used when the
        # response shape genuinely changes; say so in the PR that runs it.
        payload = {"_meta": _fixture_meta(), "variants": fixture_variants}
        fixture_path.write_text(
            json.dumps(payload, indent=2, sort_keys=False),
            encoding="utf-8",
        )
        # The freshly written file must round-trip through the parser the
        # compare path uses (guards against a malformed write).
        written = json.loads(fixture_path.read_text(encoding="utf-8"))
        assert written["variants"] == fixture_variants
        parsed_variants = written["variants"]
    else:
        # Drift-check path: on-disk fixture must exist and match the fresh
        # capture. Missing = FAIL (S1); mismatch = FAIL (W1/W11 — the old
        # test overwrote the fixture and asserted the read-back, which
        # could never detect drift).
        assert fixture_path.exists(), (
            f"Fixture {FIXTURE_REL} is MISSING. It is a committed contract "
            "artifact (the shape PR3 matches), not a generated byproduct — "
            "produce it deliberately with: REGENERATE_FIXTURE=1 uv run pytest "
            "tests/integration/test_messages_response_fixture_capture.py"
        )
        on_disk = json.loads(fixture_path.read_text(encoding="utf-8"))
        assert isinstance(on_disk, dict) and "variants" in on_disk, (
            "On-disk fixture has the pre-v2 schema (bare list). Regenerate "
            "deliberately to add the _meta header: REGENERATE_FIXTURE=1 "
            "uv run pytest tests/integration/test_messages_response_fixture_capture.py"
        )
        assert on_disk["variants"] == fixture_variants, (
            "FIXTURE DRIFT: the on-disk fixture no longer matches a fresh "
            "capture of the current code path. If the response shape "
            "genuinely changed, regenerate deliberately (REGENERATE_FIXTURE=1) "
            "and call it out in the PR; otherwise fix the code, not the "
            f"fixture.\non-disk: {json.dumps(on_disk['variants'], indent=2)}\n"
            f"fresh  : {json.dumps(fixture_variants, indent=2)}"
        )
        # S3 — the environment the fixture was captured under must match
        # the current one; a library upgrade requires regeneration.
        on_disk_packages = on_disk.get("_meta", {}).get("packages")
        assert on_disk_packages == _fixture_meta()["packages"], (
            "FIXTURE ENV DRIFT: package versions differ from the fixture's "
            f"_meta header (fixture: {on_disk_packages}). Regenerate "
            "deliberately if the upgrade is intended."
        )
        parsed_variants = on_disk["variants"]

    # ── ASSERTIONS on the (written or loaded) variants ─────────────────────
    # (a) Fixture is valid with the expected top-level shape.
    assert isinstance(parsed_variants, list)
    assert len(parsed_variants) == 4  # exactly 4 variants

    # Every captured message is a dict and carries message_id (the frontend
    # anchor contract). id-less variant falls back to a UUID at serialization,
    # normalized to "<generated-uuid>" by the writer.
    for entry in parsed_variants:
        assert "variant_id" in entry
        assert "messages" in entry
        assert "observed_alist_count" in entry
        assert isinstance(entry["messages"], list)
        for msg in entry["messages"]:
            assert isinstance(msg, dict)
            assert "message_id" in msg, (
                f"Variant {entry['variant_id']} produced a message without message_id: {msg}"
            )
            assert "role" in msg
            assert "content" in msg  # normalized sentinel, but always present
            assert "instance_id" in msg

    # (c) Observed alist_count baseline — REPORTED to the test log. The
    # dispatcher reads this from the test output and reports it upward
    # for the production gate. Each variant should produce ≥1 checkpoint
    # tuple (aupdate_state writes at least one checkpoint).
    print("\n[Fixture Capture] pre-C1 alist_count baseline per variant:")
    for entry in parsed_variants:
        print(
            f"  - {entry['variant_id']}: "
            f"alist_count={entry['observed_alist_count']} "
            f"bytes_estimate={entry['bytes_estimate']} "
            f"messages={len(entry['messages'])}"
        )

    # (d) Sanity: alist_count is at least 1 per variant (aupdate_state wrote
    # at least one checkpoint). This is the pre-C1 baseline the production
    # gate compares against (post-C1 the count collapses to 0 by absence).
    for entry in parsed_variants:
        assert entry["observed_alist_count"] >= 1, (
            f"Variant {entry['variant_id']} observed alist_count="
            f"{entry['observed_alist_count']} — expected >=1 since "
            "aupdate_state wrote at least one checkpoint"
        )
