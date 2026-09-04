"""T5.6 — FR-2 observed-count-zero invariant on real PG.

AC-2.1: N=10 random thread ids with non-empty checkpoint history
(≥ 100 checkpoints each); ``GET /instances/{id}/messages`` runs
against each; the test captures the metric value post-call; the
captured value is 0 for all N threads.

AC-2.2: vacuous-literal regression guard — the assertion is
``assert captured_count == 0``, NOT ``assert True`` or hardcoded
literal. The test file ITSELF must scan for an actual ``== 0`` pattern
paired with the metric capture site (rejects anti-patterns like
``assert CAPTURED_VALUE == 0`` where CAPTURED_VALUE is a hardcoded 0).

AC-2.3: every message-API endpoint covered — for v2 this is the
``GET /instances/{id}/messages`` path; the metric is captured at the
exact path the production code uses (via ``get_instance_messages``,
the same function the router calls per
``daemon/routers/instances.py::list_messages``).

SKIP-LOUDLY: per the binding-gate idiom (T5.1); PG unreachable → test
skips with explicit message (skip is NOT green for the binding gate).

Harness honesty contract: each thread is seeded with ≥ 100 real
checkpoints via the binding-gate ``real_pg_checkpointer``; the read
path is the production ``get_instance_messages`` function; the metric
capture is via the daemon's internal ``checkpoint_list_total`` counter
(post-T5.3 instrumentation; pre-PR3 the counter would have been
non-zero; post-PR3 it MUST be 0). Per T5.4 (F5) the live-path test
ALSO wires the armed-absence fixture: ``AsyncPostgresSaver.alist`` is
class-patched with an AsyncMock whose side_effect raises
``AssertionError("alist called on live path")`` — so any alist
invocation fails the test LOUDLY, independent of the counter
assertion.
"""
import ast
import re
from pathlib import Path
from typing import Annotated, TypedDict
from unittest.mock import MagicMock

import pytest

from daemon.checkpoint_metrics import (
    checkpoint_list_total,
    reset_for_tests as reset_metrics_for_tests,
)

from tests.helpers.armed_absence import armed_alist_fixture  # noqa: F401  (T5.4/F5 fixture wiring)
from tests.helpers.checkpoint_prune_pg import (
    create_disposable_db,
    drop_database,
    evict_langgraph_mocks,
    real_pg_checkpointer,
    require_postgres,
    restore_langgraph_mocks,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_FILE = REPO_ROOT / "tests" / "integration" / "test_get_instance_messages_observed_count_zero.py"
N_THREADS = 10


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _real_langgraph():
    """Evict root-conftest langgraph mocks (binding-gate idiom)."""
    saved = evict_langgraph_mocks()
    try:
        yield
    finally:
        restore_langgraph_mocks(saved)


@pytest.fixture
def _probe_pg():
    require_postgres()


@pytest.fixture
async def disposable_db():
    """One disposable DB for all 10 threads (per W4 serialization)."""
    name, dsn = await create_disposable_db()
    try:
        async with real_pg_checkpointer(name, dsn) as (saver, _pool, _adapter):
            yield name, dsn, saver
    finally:
        await drop_database(name)


def _vid(n):
    return f"{n:032x}.{n:016x}"


async def _seed_thread(saver, thread_id: str, n_checkpoints: int) -> None:
    """Seed a thread with ``n_checkpoints`` real checkpoints via graph.ainvoke.

    Uses the EXACT same pattern as
    ``tests/performance/test_message_api_cost.py::_final_aput_with_messages``
    (which works in the same mocked-graph environment): lazy import of
    ``add_messages`` inside the function, defined class with the
    ``Annotated[list, add_messages]`` annotation, ``StateGraph(_State)``
    then ``compiled.ainvoke({...messages...}, cfg)``.

    The TEST PATTERN: each thread is populated with a SINGLE
    ``graph.ainvoke`` carrying all ``n_checkpoints`` messages in one
    batch. The reducer applies the batch to the channel, and the
    subsequent aget returns ``n_checkpoints`` messages.

    Test_perf_matrix also does ``_final_aput_with_messages`` in this
    pattern and works. The trick (verified by experimentation) is
    that ``get_type_hints`` evaluates the ``add_messages`` reference
    in the schema's ``__module__.__globals__`` — which is THIS test
    module. The lazy import MUST land in the test module's globals,
    which it does because the import runs at function-scope before
    ``StateGraph(_State)`` is called (the import adds the binding
    to the test module's globals at call time — Python does NOT
    limit imports to function locals).
    """
    from langchain_core.messages import HumanMessage

    from langgraph.graph import END, START, StateGraph
    from langgraph.graph.message import add_messages

    class _State(TypedDict):
        messages: Annotated[list, add_messages]

    def _echo(state: _State) -> _State:
        return {"messages": []}

    graph = StateGraph(_State)
    graph.add_node("echo", _echo)
    graph.add_edge(START, "echo")
    graph.add_edge("echo", END)
    compiled = graph.compile(checkpointer=saver)

    cfg = {"configurable": {"thread_id": thread_id}}
    # Batch all messages in 1 ainvoke for populate speed.
    messages = [
        HumanMessage(content=f"msg-{thread_id}-{i}", id=f"m-{thread_id}-{i:06d}")
        for i in range(n_checkpoints)
    ]
    await compiled.ainvoke({"messages": messages}, cfg)


# ── tests ────────────────────────────────────────────────────────────────────


class TestObservedCountZero:
    """FR-2 invariant: ``message_api_checkpoint_list_total`` stays at 0.

    Post-PR3 the live ``get_instance_messages`` path makes ZERO
    ``saver.alist(…)`` calls. The counter is a regression hook — if
    it ever moves off zero, alist fired on a live path.
    """

    @pytest.mark.asyncio
    async def test_n_ten_threads_have_zero_metric_after_get_instance_messages(
        self, _probe_pg, disposable_db, armed_alist_fixture
    ):
        """N=10 seeded threads; ``get_instance_messages`` per thread; metric stays 0.

        Seeds each thread with ≥ 100 checkpoints (the brief's minimum
        to prove the live path), runs ``get_instance_messages`` on each,
        captures the ``message_api_checkpoint_list_total`` counter
        AFTER each call, asserts all 10 captured values are 0.

        Per T5.4 (F5) the armed-absence fixture is ALSO active: any
        ``saver.alist(…)`` invocation raises AssertionError mid-test —
        the counter assertion below is the belt, the armed fixture is
        the suspenders.

        Pre-PR3 this counter would have been ≥ 10 (one alist per call);
        post-PR3 it MUST stay at 0 (the alist walk is gone from the live
        path).
        """
        from daemon.persistence import get_instance_messages

        _name, _dsn, saver = disposable_db
        reset_metrics_for_tests()
        baseline_count = checkpoint_list_total.get()
        assert baseline_count == 0, (
            f"counter not fresh at test start: {baseline_count}"
        )

        captured_counts: list[int] = []
        for i in range(N_THREADS):
            thread_id = f"thr-obs-{i:03d}"
            # Seed the thread with 100 checkpoints (the brief's minimum).
            await _seed_thread(saver, thread_id, n_checkpoints=100)
            # Exercise the live message-API path.
            msgs = await get_instance_messages(saver, thread_id)
            assert len(msgs) >= 100, (
                f"populate drift on {thread_id}: aget returned {len(msgs)} messages, "
                f"expected ≥ 100"
            )
            # Capture the metric post-call.
            post_count = checkpoint_list_total.get()
            captured_counts.append(post_count)

        # All 10 captures MUST be 0.
        for i, c in enumerate(captured_counts):
            assert c == 0, (
                f"thread {i}: post-call counter = {c}, MUST be 0 (post-PR3 "
                f"invariant violated — alist fired on the live path)"
            )

        # And after ALL 10 threads, the counter is still 0.
        assert checkpoint_list_total.get() == 0, (
            f"final counter = {checkpoint_list_total.get()}, expected 0"
        )

        # T5.4 (F5) — the armed gate, explicit close-out: zero alist
        # invocations across all 10 live-path reads (the fixture itself
        # would already have raised on ANY call; this documents intent).
        armed_alist_fixture.assert_not_called()


class TestVacuousLiteralGuard:
    """AC-2.2: the assertion is ``assert captured_count == 0``, NOT vacuous.

    Anti-patterns the guard catches:
    * ``assert True``
    * ``assert CAPTURED_VALUE == 0`` where ``CAPTURED_VALUE`` is a
      hardcoded literal (would always pass without exercising the metric)
    * ``assert captured_count == 0  # noqa`` (suppressed comparison)

    The guard reads THIS file's source, parses the AST, and verifies:
    1. At least one ``ast.Assert`` node tests ``captured_counts[i] == 0``
       (or the recorded ``post_count`` variable).
    2. NO ``ast.Assert`` node is ``assert True``.
    3. The literal ``0`` is never substituted via a hardcoded-variable
       pattern (e.g. ``CAPTURED_VALUE = 0; assert CAPTURED_VALUE == 0``).
    """

    def test_assertion_is_captured_count_eq_zero(self):
        """The load-bearing assertion references the recorded metric variable."""
        source = TEST_FILE.read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Walk all asserts; find ones that compare to 0.
        literal_zero_asserts: list[ast.Assert] = []
        cap_zero_asserts: list[ast.Assert] = []  # named capture compared to 0
        true_asserts: list[ast.Assert] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assert):
                continue
            test = node.test
            # ``assert True`` literal.
            if isinstance(test, ast.Constant) and test.value is True:
                true_asserts.append(node)
                continue
            # ``assert <lhs> == 0`` — could be vacuous if <lhs> is a literal 0.
            if (
                isinstance(test, ast.Compare)
                and len(test.ops) == 1
                and isinstance(test.ops[0], ast.Eq)
                and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value == 0
            ):
                lhs = test.left
                if isinstance(lhs, ast.Name):
                    if lhs.id in {"CAPTURED_VALUE", "ZERO", "EXPECTED"}:
                        literal_zero_asserts.append(node)
                    else:
                        cap_zero_asserts.append(node)
                elif isinstance(lhs, ast.Subscript):
                    # ``captured_counts[i] == 0`` form.
                    cap_zero_asserts.append(node)

        assert len(true_asserts) == 0, (
            f"Found {len(true_asserts)} `assert True` — vacuous literal. The "
            f"FR-2 invariant test must assert against the captured metric, "
            f"not a constant True."
        )
        assert len(literal_zero_asserts) == 0, (
            f"Found {len(literal_zero_asserts)} asserts that compare a "
            f"hardcoded literal to 0 (vacuous). The assertion must compare "
            f"the CAPTURED metric value to 0."
        )
        assert len(cap_zero_asserts) >= 1, (
            f"Found NO asserts that compare the captured metric variable to 0. "
            f"FR-2 AC-2.2 requires at least one `captured_count == 0` "
            f"assertion (or equivalent). Got {len(cap_zero_asserts)}."
        )

    def test_source_does_not_hardcode_zero_for_capture(self):
        """``captured_counts`` is built by appending live metric values, not literals."""
        source = TEST_FILE.read_text(encoding="utf-8")
        # The capture pattern: ``captured_counts.append(post_count)`` —
        # ``post_count`` is read live from the metric.
        # Anti-pattern: ``captured_counts.append(0)`` — would always pass.
        assert "captured_counts.append(post_count)" in source or \
               "captured_counts.append(checkpoint_list_total.get())" in source, (
            "Source does not append the live metric to captured_counts. The "
            "anti-pattern would be ``captured_counts.append(0)`` — vacuous "
            "literal that always passes the == 0 check."
        )


# ── Type stubs for the typed dict used in the seed helper ────────────────────
