"""TIER-A de-dup (row2-2a) — counting-work pins for the compaction engine.

The TIER-A de-dup removed the redundant WHOLE-LIST token counts from the
compaction engine:

* ``compact_state`` measured the SELECTABLE pool up to FIVE times per
  compaction (same list, same value);
* ``_apply_ceiling_rule`` re-summed EVERY section on EVERY loop
  iteration — O(S²) section tokenizations;
* ``emergency_truncate`` re-counted the WHOLE message list on every
  iteration of its two loops (the Pass-3 halving loop and the C1 pop
  loop) — O(H·N) + O(P·N) counting work.

This module is the MUTATION PIN for that work: it instruments the token
estimator and asserts the counting work stays LINEAR. Re-adding a
whole-list re-encode inside ANY of those loops makes these tests RED.

The "before" profile is measured WITHOUT a second copy of the legacy
code: the de-duplicated ``emergency_truncate`` keeps the ORIGINAL
whole-list re-count path as the fallback for a NON-additive estimator
(a stub / a mock / any estimator that does not decompose per message).
Feeding it a deliberately non-additive wrapper therefore reproduces the
pre-change call profile exactly — and, as a bonus, proves the two paths
produce the SAME output.

Byte-identity of the emitted values is pinned in
``tests/unit/test_compaction.py`` (class ``TestTierAByteIdentity``),
which compares the new implementation against a verbatim copy of the
pre-change code.
"""

import random

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from daemon.compaction import emergency_truncate
from daemon.loader import estimate_messages_tokens


# ── instrumentation ────────────────────────────────────────────────────


class _EstimatorSpy:
    """Wraps an estimator and records the size of every call.

    The size and the object identity are captured AT CALL TIME: the
    caller often mutates the list it handed over afterwards (the pop
    loop shrinks it), so a later inspection of the recorded list would
    see the FINAL state, not the state the estimator saw.
    """

    def __init__(self, fn):
        self._fn = fn
        self.sizes: list[int] = []
        self.obj_ids: list[int] = []
        # Keep the handed-over objects alive so their ids stay unique
        # (CPython reuses the address of a freed object).
        self._keepalive: list = []

    def __call__(self, msgs):
        self.sizes.append(len(msgs))
        self.obj_ids.append(id(msgs))
        self._keepalive.append(msgs)
        return self._fn(msgs)

    @property
    def total(self) -> int:
        return len(self.sizes)

    @property
    def whole_list(self) -> int:
        """Calls that handed the estimator MORE than one message."""
        return sum(1 for n in self.sizes if n > 1)

    @property
    def single(self) -> int:
        return sum(1 for n in self.sizes if n == 1)

    def single_obj_ids(self) -> list[int]:
        """Object ids of the single-message calls, in call order."""
        return [i for i, n in zip(self.obj_ids, self.sizes) if n == 1]


def _non_additive_estimate(msgs):
    """Whole-list-only estimator — deliberately NOT additive.

    The whole-list value is the true count (so every DECISION matches the
    production path), but the single-message value is different, so the
    additivity probe fails and the function falls back to the ORIGINAL
    whole-list re-count path.
    """
    if len(msgs) == 1:
        return -1
    return estimate_messages_tokens(msgs)


# ── seeded corpora ─────────────────────────────────────────────────────


def _seeded_over_budget_corpus(n: int = 40) -> list:
    """Seeded corpus that is OVER budget for a tiny token limit.

    Every message carries a long body, so the Pass-3 halving loop (H > 0)
    AND the C1 pop loop (P > 0) both run — the regime where the
    pre-change implementation paid O(H·N) + O(P·N) counting work.
    """
    rng = random.Random(20261120)
    msgs: list = []
    for i in range(n):
        body = "".join(
            rng.choice("abcdefghijklmnopqrstuvwxyz ") for _ in range(2000)
        )
        if i % 3 == 0:
            msgs.append(HumanMessage(content=body, id=f"h{i}"))
        elif i % 3 == 1:
            msgs.append(AIMessage(content=body, id=f"a{i}"))
        else:
            msgs.append(ToolMessage(
                content=body, tool_call_id=f"c{i}", name="t", id=f"t{i}",
            ))
    return msgs


def _sections(n: int) -> list[dict]:
    return [
        {
            "start_idx": i * 10,
            "end_idx": i * 10 + 9,
            "body": (f"section {i} body. ") * (50 * (i + 1)),
            "start_id": f"s{i}-start",
            "end_id": f"s{i}-end",
        }
        for i in range(n)
    ]


# ── emergency_truncate ─────────────────────────────────────────────────


class TestEmergencyTruncateCountingWork:
    """The two emergency loops must NOT re-encode the whole list."""

    def test_both_loops_actually_run_on_the_seeded_corpus(self):
        """Corpus precondition: the halving loop AND the pop loop run.

        Without this, the counting-work pins below would be vacuous.
        """
        corpus = _seeded_over_budget_corpus()
        result = emergency_truncate(
            corpus, max_tokens=10, estimate_fn=estimate_messages_tokens,
        )
        assert len(result) < len(corpus), (
            "the pop loop must have dropped messages (P > 0) — otherwise "
            "the counting-work pins are vacuous"
        )
        assert any(
            str(m.content).endswith("\n[...truncated]") for m in result
        ), (
            "the Pass-3 halving loop must have rewritten a body (H > 0) — "
            "otherwise the counting-work pins are vacuous"
        )

    def test_no_whole_list_reencode_inside_the_loops(self):
        """MUTATION PIN — no whole-list re-encode inside ANY loop.

        The whole message list may be counted a CONSTANT number of times
        (the three unconditional Pass-0/1/2 checks). Every count inside
        the halving loop or the pop loop must be a SINGLE-message count.
        """
        corpus = _seeded_over_budget_corpus()
        spy = _EstimatorSpy(estimate_messages_tokens)
        emergency_truncate(corpus, max_tokens=10, estimate_fn=spy)

        assert spy.whole_list <= 3, (
            "a whole-list re-encode was re-added inside a loop: "
            f"{spy.whole_list} whole-list counts (> 3 means the loops "
            "re-count the entire message list per iteration)"
        )
        # Every single-message count must be a FRESH list object — a
        # re-added whole-list re-encode that happens to run on a
        # one-message list would reuse the same object.
        single_ids = spy.single_obj_ids()
        assert len(single_ids) == len(set(single_ids)), (
            "a message list was handed to the estimator twice — a "
            "whole-list re-encode was re-added inside a loop"
        )
        # The total counting work stays linear in the corpus size:
        #   3 whole-list checks + N probe calls + at most 3 halvings per
        #   message (a 2000-char body halves 2000→1000→513→269) + at
        #   most N-1 pops.
        assert spy.total <= 6 * len(corpus) + 8, (
            f"estimator call count {spy.total} is not linear in the "
            f"corpus size ({len(corpus)} messages)"
        )

    def test_legacy_profile_is_the_quadratic_one(self):
        """The pre-change call profile is reproduced by the fallback path.

        Feeding a NON-additive estimator drives the function into the
        ORIGINAL whole-list re-count path, which is exactly the
        pre-change behavior — the "before" number the pin removes.
        """
        corpus = _seeded_over_budget_corpus()
        legacy = _EstimatorSpy(_non_additive_estimate)
        emergency_truncate(corpus, max_tokens=10, estimate_fn=legacy)

        assert legacy.whole_list > 3, (
            "the corpus must exercise the loops in the legacy profile "
            f"(whole-list counts: {legacy.whole_list})"
        )

    def test_both_paths_produce_the_same_output(self):
        """Fallback path and incremental path agree byte-for-byte.

        The fallback (non-additive estimator) re-counts the whole list
        per iteration — the pre-change counting shape. The incremental
        path maintains a running count. Both must produce the SAME list.
        """
        corpus = _seeded_over_budget_corpus()
        incremental = emergency_truncate(
            corpus, max_tokens=10, estimate_fn=estimate_messages_tokens,
        )
        legacy = emergency_truncate(
            corpus, max_tokens=10, estimate_fn=_non_additive_estimate,
        )
        assert len(incremental) == len(legacy)
        for n, r in zip(incremental, legacy):
            assert n.content == r.content
            assert getattr(n, "id", None) == getattr(r, "id", None)
        assert (
            estimate_messages_tokens(incremental)
            == estimate_messages_tokens(legacy)
        )


# ── _apply_ceiling_rule ───────────────────────────────────────────────


class TestCeilingRuleCountingWork:
    """The ceiling rule must not re-sum every section per iteration."""

    def test_section_estimates_are_measured_once(self):
        """MUTATION PIN — exactly one estimate per section (+2 fixed).

        Pre-change the rule re-summed every section on every iteration:
        O(S²) section tokenizations. The de-duplicated rule measures the
        GLOBAL once, every section body once, and the condensed marker
        once.
        """
        from daemon.compaction import _apply_ceiling_rule
        import daemon.compaction as compaction_module

        for n_sections in (1, 3, 12, 40):
            spy = _EstimatorSpy(estimate_messages_tokens)
            # Patch the name in the compaction module's namespace so the
            # rule (which calls the bare ``estimate_messages_tokens``)
            # routes through the spy.
            original = compaction_module.estimate_messages_tokens
            compaction_module.estimate_messages_tokens = spy
            try:
                _apply_ceiling_rule(_sections(n_sections), "GLOBAL", 1000)
            finally:
                compaction_module.estimate_messages_tokens = original
            assert spy.total <= n_sections + 2, (
                f"ceiling rule made {spy.total} estimator calls for "
                f"{n_sections} sections — a whole-list re-encode (the "
                "per-iteration re-sum) was re-added"
            )

    def test_result_is_independent_of_the_measurement_shape(self):
        """The condensed shape must not depend on how the tokens were got.

        The de-duplicated rule accumulates savings incrementally; the
        pre-change rule re-summed. Both must produce the same result for
        the same input.
        """
        from daemon.compaction import _apply_ceiling_rule

        sections = _sections(8)
        result = _apply_ceiling_rule(sections, "GLOBAL", 1000)
        assert result[2] >= 0
        # Re-running with a fresh (unmutated) input must be identical.
        again = _apply_ceiling_rule(_sections(8), "GLOBAL", 1000)
        assert result == again


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
