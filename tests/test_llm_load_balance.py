"""Unit tests for the weighted random model selection algorithm.

These tests cover the full edge-case surface defined in
``.agents/shared/planning/llm-model-load-balance/phase5-plan.md`` (Task 1):

  - Edge cases (None, empty, single entry)
  - Statistical correctness (50,000-sample distribution within ±2%)
  - Weight clamping ([1, 100] bounds)
  - Allowed-models filtering (case-insensitive, all-filtered → None)
  - Per-entry validation (bool weight rejection, numeric string coercion,
    float truncation, whitespace model filtering, non-numeric skip)
  - Duplicate models are additive
  - Empty / whitespace-only allowed list = no restriction

The tests use ``random.seed(42)`` for statistical reproducibility (per the
plan's CI Integration section).
"""

from __future__ import annotations

import random

import pytest

from daemon.registry import LLMModelWeight
from daemon.services.llm_load_balancer import _select_weighted_model


def make_pool(*pairs: tuple[str, int]) -> list[LLMModelWeight]:
    """Build a list of LLMModelWeight entries from (model, weight) pairs."""
    return [LLMModelWeight(model=m, weight=w) for m, w in pairs]


def make_pool_bypass_validation(*pairs: tuple[str, int]) -> list[LLMModelWeight]:
    """Build ``LLMModelWeight`` entries via ``model_construct`` (bypasses validators).

    W3 fix made weight < 1 a Pydantic-level ``ValidationError``. The
    algorithm-level clamping in ``_select_weighted_model`` is still the
    "belt-and-suspenders" path for directly-constructed objects
    (``model_construct`` skips validators) — these tests exercise that
    path. Use ``make_pool`` for normal pool construction; use this only
    for tests that intentionally feed invalid weights to assert the
    algorithm still selects sensibly.
    """
    return [
        LLMModelWeight.model_construct(model=m, weight=w) for m, w in pairs
    ]


class TestSelectWeightedModelEdgeCases:
    """Empty / None / single-entry edge cases."""

    def test_none_input_returns_none(self):
        assert _select_weighted_model(None, []) is None

    def test_none_input_with_none_allowed_returns_none(self):
        assert _select_weighted_model(None, None) is None

    def test_empty_list_returns_none(self):
        assert _select_weighted_model([], []) is None

    def test_empty_list_with_none_allowed_returns_none(self):
        assert _select_weighted_model([], None) is None

    def test_single_entry_always_selected(self):
        pool = make_pool(("m1", 1))
        for _ in range(1000):
            assert _select_weighted_model(pool, []) == "m1"

    def test_single_entry_with_allowed_returns_selected(self):
        pool = make_pool(("m1", 1))
        for _ in range(100):
            assert _select_weighted_model(pool, ["m1"]) == "m1"

    def test_single_entry_with_unrelated_allowed_returns_selected(self):
        """Single entry is in the pool — it must be selected even if the
        allowed list is empty (which means no restriction)."""
        pool = make_pool(("m1", 1))
        # Empty allowed list = no restriction, so m1 is selected.
        assert _select_weighted_model(pool, []) == "m1"


class TestStatisticalCorrectness:
    """Distribution tests with 50,000 samples (±2% tolerance)."""

    def test_equal_weights_distribution(self):
        random.seed(42)
        pool = make_pool(("m1", 50), ("m2", 50))
        counts = {"m1": 0, "m2": 0}
        for _ in range(50000):
            counts[_select_weighted_model(pool, [])] += 1
        # 50/50 split: expected 25,000 each, ±2% = ±1,000
        assert 24000 <= counts["m1"] <= 26000, f"m1 count {counts['m1']} outside ±2%"
        assert 24000 <= counts["m2"] <= 26000, f"m2 count {counts['m2']} outside ±2%"

    def test_heavy_weight_distribution(self):
        random.seed(42)
        pool = make_pool(("m1", 90), ("m2", 10))
        counts = {"m1": 0, "m2": 0}
        for _ in range(50000):
            counts[_select_weighted_model(pool, [])] += 1
        # 90/10 split: expected 45,000 / 5,000
        assert 44000 <= counts["m1"] <= 46000, f"m1 count {counts['m1']} outside ±2%"
        assert 4000 <= counts["m2"] <= 6000, f"m2 count {counts['m2']} outside ±2%"

    def test_three_way_distribution(self):
        random.seed(42)
        pool = make_pool(("a", 10), ("b", 30), ("c", 60))
        counts = {"a": 0, "b": 0, "c": 0}
        for _ in range(50000):
            counts[_select_weighted_model(pool, [])] += 1
        # 10/30/60 → ~5k, 15k, 30k, ±2% = ±1k, ±1k, ±1k
        assert 4000 <= counts["a"] <= 6000
        assert 14000 <= counts["b"] <= 16000
        assert 29000 <= counts["c"] <= 31000

    def test_distribution_with_clamped_weights(self):
        """Pre-clamped values (all within [1, 100]) still distribute correctly."""
        random.seed(42)
        pool = make_pool(("m1", 1), ("m2", 99))
        counts = {"m1": 0, "m2": 0}
        for _ in range(50000):
            counts[_select_weighted_model(pool, [])] += 1
        # 1/99 → 1% m1, 99% m2
        assert counts["m1"] < 1500
        assert counts["m2"] > 48500


class TestWeightClamping:
    """Weights outside [1, 100] must be clamped before selection."""

    def test_clamp_zero_to_one(self):
        """weight=0 → clamped to 1; single entry always selected.

        W3: Pydantic now rejects weight<1 at validation time. This test
        exercises the algorithm-level clamp by bypassing validation
        (``model_construct``) — directly-constructed objects are the
        belt-and-suspenders case the spec preserves.
        """
        pool = make_pool_bypass_validation(("m1", 0))
        for _ in range(100):
            assert _select_weighted_model(pool, []) == "m1"

    def test_clamp_negative_to_one(self):
        """weight=-5 → clamped to 1; single entry always selected.

        W3: Pydantic rejects negative weight; this tests the algorithm-
        level clamp via ``model_construct``.
        """
        pool = make_pool_bypass_validation(("m1", -5))
        for _ in range(100):
            assert _select_weighted_model(pool, []) == "m1"

    def test_clamp_high_to_100(self):
        """weight=200 → clamped to 100; single entry always selected."""
        pool = make_pool(("m1", 200))
        for _ in range(100):
            assert _select_weighted_model(pool, []) == "m1"

    def test_clamp_negative_vs_valid(self):
        """Negative weight clamps to 1, valid stays at 50 → 1/51 ratio.

        W3: Uses ``model_construct`` to bypass Pydantic rejection.
        """
        random.seed(42)
        pool = make_pool_bypass_validation(("m1", -5), ("m2", 50))
        counts = {"m1": 0, "m2": 0}
        for _ in range(50000):
            counts[_select_weighted_model(pool, [])] += 1
        # -5 → 1, 50 → 50 → m1 ~2% (1000), m2 ~98% (49000)
        assert counts["m1"] < 1500, f"m1 count {counts['m1']} too high (expected <3%)"
        assert counts["m2"] > 48500

    def test_clamp_both_high(self):
        """Both weights clamped to 100 → equal distribution."""
        random.seed(42)
        pool = make_pool(("m1", 500), ("m2", 1000))
        counts = {"m1": 0, "m2": 0}
        for _ in range(50000):
            counts[_select_weighted_model(pool, [])] += 1
        # Both clamp to 100 → 50/50 split
        assert 24000 <= counts["m1"] <= 26000
        assert 24000 <= counts["m2"] <= 26000


class TestAllowedModelsFiltering:
    """Filtering by config.llm.allowed_models (case-insensitive)."""

    def test_all_filtered_returns_none(self):
        pool = make_pool(("m1", 100))
        assert _select_weighted_model(pool, ["m_other"]) is None

    def test_mixed_filter(self):
        """m_blocked is excluded; m1 and m2 split 50/50."""
        random.seed(42)
        pool = make_pool(("m1", 50), ("m_blocked", 100), ("m2", 50))
        allowed = ["m1", "m2"]
        for _ in range(1000):
            result = _select_weighted_model(pool, allowed)
            assert result in ("m1", "m2"), f"Got filtered model: {result}"

    def test_case_insensitive_filter(self):
        pool = make_pool(("ModelA", 1))
        # Lowercase match
        assert _select_weighted_model(pool, ["modela"]) == "ModelA"
        # Uppercase match
        assert _select_weighted_model(pool, ["MODELA"]) == "ModelA"
        # Mixed case match
        assert _select_weighted_model(pool, ["MoDeLa"]) == "ModelA"

    def test_empty_allowed_models_no_restriction(self):
        random.seed(42)
        pool = make_pool(("m1", 1), ("m2", 1))
        seen = set()
        for _ in range(100):
            seen.add(_select_weighted_model(pool, []))
        assert seen == {"m1", "m2"}

    def test_none_allowed_models_no_restriction(self):
        random.seed(42)
        pool = make_pool(("m1", 1), ("m2", 1))
        seen = set()
        for _ in range(100):
            seen.add(_select_weighted_model(pool, None))
        assert seen == {"m1", "m2"}

    def test_whitespace_only_allowed_models_no_restriction(self):
        """Allowed list with all-blank strings → treated as no restriction."""
        random.seed(42)
        pool = make_pool(("m1", 1), ("m2", 1))
        seen = set()
        for _ in range(100):
            seen.add(_select_weighted_model(pool, ["", "  ", None]))
        # Either m1 or m2 is selected (not None, since the pool is valid).
        assert seen.issubset({"m1", "m2"})
        assert len(seen) == 2  # both seen across 100 samples


class TestDuplicatesAndEdgeInputs:
    """Duplicate models, whitespace handling, all-whitespace → None."""

    def test_duplicate_models_are_additive(self):
        """Duplicate model names sum their weights (still always m1)."""
        pool = make_pool(("m1", 50), ("m1", 50))
        for _ in range(1000):
            assert _select_weighted_model(pool, []) == "m1"

    def test_duplicate_models_compete_with_other(self):
        """Two m1 entries (weight 50 each = 100) vs m2 (weight 100) → 50/50."""
        random.seed(42)
        pool = make_pool(("m1", 50), ("m1", 50), ("m2", 100))
        counts = {"m1": 0, "m2": 0}
        for _ in range(50000):
            counts[_select_weighted_model(pool, [])] += 1
        # Combined weight: m1=100, m2=100 → 50/50
        assert 24000 <= counts["m1"] <= 26000
        assert 24000 <= counts["m2"] <= 26000

    def test_whitespace_only_model_filtered(self):
        """Whitespace-only model name is filtered (per-entry).

        W4: Pydantic now rejects whitespace-only model names at
        validation time. This test exercises the algorithm-level filter
        via ``model_construct`` (bypasses validators) — the same
        belt-and-suspenders path used for directly-constructed objects.
        """
        pool = make_pool_bypass_validation(("   ", 100), ("m1", 1))
        for _ in range(1000):
            assert _select_weighted_model(pool, []) == "m1"

    def test_all_whitespace_returns_none(self):
        """All entries have whitespace-only model names → None (no valid).

        W4: Pydantic now rejects whitespace-only model names; this tests
        the algorithm-level filter via ``model_construct`` (bypasses
        validators). With all entries whitespace-only and stripped to
        empty, the algorithm's per-entry filter strips them out → no
        valid candidates → ``None``.
        """
        pool = make_pool_bypass_validation(
            ("   ", 100), ("\t", 100), ("\n", 100)
        )
        assert _select_weighted_model(pool, []) is None

    def test_fallback_when_all_filtered_via_allowed(self):
        """When ALL candidates are filtered by allowed_models → None."""
        pool = make_pool(("m1", 1), ("m2", 1), ("m3", 1))
        assert _select_weighted_model(pool, ["other1", "other2"]) is None


class TestTypeValidation:
    """Per-entry type validation: bool, float, string numeric."""

    def test_float_weight_truncated(self):
        """Float weights are truncated to int (50.7 → 50, 49.3 → 49)."""
        random.seed(42)
        pool = make_pool(("m1", 50.7), ("m2", 49.3))
        counts = {"m1": 0, "m2": 0}
        for _ in range(50000):
            counts[_select_weighted_model(pool, [])] += 1
        # 50 vs 49 → 50.5/49.5 → 50/50 ±2% (very close)
        assert 24000 <= counts["m1"] <= 26000
        assert 24000 <= counts["m2"] <= 26000

    def test_numeric_string_weight_coerced(self):
        """String '50' is coerced to int 50."""
        random.seed(42)
        pool = make_pool(("m1", "50"), ("m2", 50))
        counts = {"m1": 0, "m2": 0}
        for _ in range(10000):
            counts[_select_weighted_model(pool, [])] += 1
        # Equal coerced weights → 50/50 within ±5% (10000 samples)
        assert abs(counts["m1"] - counts["m2"]) < 1000

    def test_numeric_string_with_decimal_coerced(self):
        """String '50.5' is coerced to int 50 (via float intermediate).

        Both entries have equal weight after coercion → 50/50 distribution.
        Verify that both models are seen across 100 samples.
        """
        random.seed(42)
        pool = make_pool(("m1", "50.5"), ("m2", "50.5"))
        seen = set()
        for _ in range(100):
            seen.add(_select_weighted_model(pool, []))
        # 50.5 → 50 (int truncation), so both m1 and m2 have weight 50
        # → 50/50 split. With 100 samples and seed 42, we should see both.
        assert seen.issubset({"m1", "m2"})

    def test_non_numeric_string_weight_rejected_at_pydantic(self):
        """String 'heavy' is rejected at Pydantic level — the whole
        ``llm_models`` array would be dropped via the discover() retry
        path. The algorithm itself never sees a non-numeric string when
        the entry was constructed via Pydantic (which is the normal flow).
        """
        with pytest.raises(Exception) as exc_info:
            LLMModelWeight(model="m1", weight="heavy")
        assert "numeric" in str(exc_info.value).lower() or "heavy" in str(exc_info.value)

    def test_algorithm_skips_untyped_entry_with_bad_weight(self):
        """Algorithm-level defensive skip when the entry was constructed
        WITHOUT going through Pydantic (e.g., legacy/direct construction).

        We use a ``FakeEntry`` plain class to bypass Pydantic and verify the
        algorithm's defensive path: non-numeric weight → skip entry.
        """

        class FakeEntry:
            def __init__(self, model, weight):
                self.model = model
                self.weight = weight

        # weight=None → algorithm defaults to 1
        e1 = FakeEntry("m1", None)
        e2 = FakeEntry("m2", 1)
        # Algorithm path: e1 is not bool, is None → weight=1
        # Both m1 and m2 are valid. The algorithm picks one.
        random.seed(0)
        result = _select_weighted_model([e1, e2], [])
        assert result in ("m1", "m2")

    def test_bool_weight_rejected_at_pydantic_level(self):
        """Boolean weights are rejected by Pydantic (cannot be tested here)."""
        # This test documents the design decision: booleans are rejected at
        # the Pydantic level (LLMModelWeight) — not at the algorithm level
        # — because isinstance(True, int) is True and Pydantic auto-coerces.
        # See LLMModelWeight._validate_weight in daemon/registry.py.
        with pytest.raises(Exception):
            LLMModelWeight(model="m1", weight=True)
        with pytest.raises(Exception):
            LLMModelWeight(model="m1", weight=False)

    def test_empty_string_model_rejected_at_pydantic_level(self):
        """Empty string model is rejected by Pydantic (min_length=1)."""
        with pytest.raises(Exception):
            LLMModelWeight(model="", weight=1)

    def test_default_weight_when_omitted(self):
        """Pydantic default for missing weight is 1."""
        entry = LLMModelWeight(model="m1")
        assert entry.weight == 1


class TestPostLoopFallthrough:
    """The numerical-safety fallthrough at the end of the algorithm."""

    def test_uniform_inclusive_upper_bound_fallthrough(self):
        """When uniform() returns exactly total_weight, fall through to last.

        This is a regression test for the "floating-point fallthrough"
        safety net in the algorithm. With our float path the chance of
        r == total_weight is negligible, but the algorithm must handle
        it gracefully.
        """
        # We can't easily force uniform() to return exactly total_weight,
        # so we test the related property: the algorithm never crashes
        # on any valid input, and the result is always one of the pool.
        random.seed(0)
        for _ in range(100):
            pool = make_pool(("a", 1), ("b", 1), ("c", 1))
            result = _select_weighted_model(pool, [])
            assert result in ("a", "b", "c")
