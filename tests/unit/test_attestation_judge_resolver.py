"""Unit tests for the LCA inline-LLM judge resolvers.

Phase 6 fastfollow (2026-09-07) of the leader completion attestation
feature. Covers:

* :mod:`daemon.services.attestation_judge_resolver` — boolean kill-switch
  resolver (``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED``). Parametrized
  truth table covers the canonical boolean parse that the Pattern C
  cached-global :func:`is_llm_judge_enabled` delegates to. The
  boolean-shape env has four accepted falsy spellings (``0`` /
  ``false`` / ``no`` / ``off``); everything else resolves to ``True``
  (Pattern C FAIL-ON / default-ON posture — typo'd ``yes-pls`` stays
  ON rather than tripping a silent disable).
* :mod:`daemon.services.attestation_judge_timeout_resolver` — wall-clock
  cap resolver (``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S``,
  default 25.0s, min clamp 5.0s; Pattern C sibling resolver, fail-OPEN
  on invalid, clamp on below-minimum). Operator tuning decision
  2026-09-07 grounded in the tester live-LLM probe; rationale in
  ``docs/setup.md`` + ``.agents/shared/planning/leader-completion-attestation/decisions.md``.

Both resolvers share the restart-read semantics — the cached-global
resolver wins over mid-flight env mutation; the test-only reset helper
re-resolves.
"""
from __future__ import annotations

import logging

import pytest

from daemon.services.attestation_judge_resolver import (
    DEFAULT_LLM_JUDGE_ENABLED,
    ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED_ENV,
    _LLM_JUDGE_FALSY,
    _parse_llm_judge_enabled,
    is_llm_judge_enabled,
    reset_llm_judge_resolver_for_tests,
)
from daemon.services.attestation_judge_timeout_resolver import (
    DEFAULT_JUDGE_TIMEOUT_S,
    ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV,
    MIN_JUDGE_TIMEOUT_S,
    _parse_judge_timeout_s,
    get_judge_timeout_s,
    reset_judge_timeout_resolver_for_tests,
)


# ─────────────────────────────────────────────────────────────────────────────
# Test fixtures + helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_judge_kill_switch(monkeypatch):
    """Hermetic kill-switch + timeout isolation per the W2 punch-list.

    Mirrors the autouse fixture in
    ``tests/unit/test_attestation_judge_wiring.py`` — clears the
    boolean kill-switch env var AND the wall-clock cap env var, and
    resets BOTH sibling resolver caches around every test so an outer
    ``.env`` / CI mutation cannot leak in and silently change the
    judge posture (kill-switch OFF, timeout too tight, etc.) for the
    assertions below.
    """
    monkeypatch.delenv(
        ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED_ENV, raising=False
    )
    monkeypatch.delenv(
        ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV, raising=False
    )
    reset_llm_judge_resolver_for_tests()
    reset_judge_timeout_resolver_for_tests()
    yield
    reset_llm_judge_resolver_for_tests()
    reset_judge_timeout_resolver_for_tests()


def _source(value: str) -> dict[str, str]:
    """Build a one-key source dict for ``_parse_llm_judge_enabled``."""
    return {ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED_ENV: value}


def _timeout_source(value: str) -> dict[str, str]:
    """Build a one-key source dict for ``_parse_judge_timeout_s``."""
    return {ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV: value}


# ─────────────────────────────────────────────────────────────────────────────
# W5 review punch-list — parametrize the truth table
# ─────────────────────────────────────────────────────────────────────────────


# Falsy spellings — the four canonical Pattern C literals PLUS a
# leading/trailing-whitespace variant to confirm the strip() engages.
_FALSY_INPUTS = [
    "0",
    "false",
    "no",
    "off",
    "FALSE",
    " 0 ",  # whitespace-padded variant — strip() must engage
]


# Truthy / default-ON inputs — empty / unset / canonical-true /
# typo'd / odd-but-non-empty values all resolve to ``True``.
_TRUTHY_INPUTS = [
    "",  # blank → default ON
    "1",
    "true",
    "yes",
    "yes-pls",  # typo'd — must NOT silently disable
    " on ",  # not in falsy set, not empty → ON
    "enabled",
]


@pytest.mark.parametrize("raw", _FALSY_INPUTS)
def test_parse_llm_judge_enabled_falsy_literals_disable(raw):
    """``0`` / ``false`` / ``no`` / ``off`` (any case, any padding) → ``False``.

    The W5 punch-list specifically pins the
    typo-doesn't-disable invariant for the **disable** half of the
    truth table: the four canonical falsy literals PLUS
    case-folded ``FALSE`` PLUS whitespace-padded ``" 0 "`` all
    disable. ``_LLM_JUDGE_FALSY`` is the single source of truth.
    """
    assert _parse_llm_judge_enabled(_source(raw)) is False
    # The four canonical falsy spellings live in the frozen set —
    # guard against a refactor that drops one.
    assert {"0", "false", "no", "off"}.issubset(_LLM_JUDGE_FALSY)


@pytest.mark.parametrize("raw", _TRUTHY_INPUTS)
def test_parse_llm_judge_enabled_default_on_for_unset_and_typos(raw):
    """Unset / blank / canonical-true / typo'd → ``True`` (FAIL-ON).

    Pattern C posture: a typo'd ``yes-pls`` stays ON rather than
    silently disabling. The judge is best-effort + fail-safe, so a
    silent OFF on a typo is the dangerous direction; ON means a
    rogue flag surfaces the conservative deny+nudge path on the
    next judge-no / unparsable / error outcome. W5 pins both halves
    of the truth table.
    """
    assert _parse_llm_judge_enabled(_source(raw)) is True


def test_parse_llm_judge_enabled_unset_returns_default():
    """Unset env → :data:`DEFAULT_LLM_JUDGE_ENABLED`` (``True``).

    The source dict lacks the env key entirely — the resolver must
    not KeyError and must return the default (NOT ``False``).
    """
    assert _parse_llm_judge_enabled({}) is DEFAULT_LLM_JUDGE_ENABLED is True


def test_parse_llm_judge_enabled_none_value_returns_default():
    """A ``None`` value (defensive — the resolver handles ``None``) → default ON."""
    assert _parse_llm_judge_enabled(
        {ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED_ENV: None}  # type: ignore[dict-item]
    ) is True


# ─────────────────────────────────────────────────────────────────────────────
# Cached-global semantics — restart-read, reset helper re-resolves
# ─────────────────────────────────────────────────────────────────────────────


def test_is_llm_judge_enabled_caches_first_resolution(monkeypatch):
    """Cached-global wins over mid-flight env mutation (restart-read).

    Pattern C discipline mirrors
    ``daemon/services/attestation_resolver.py`` — flip requires
    restart. The first call resolves and caches; subsequent calls
    return the cached value even if the env has been mutated in the
    interim.
    """
    # First read with env unset → default ON.
    assert is_llm_judge_enabled() is True
    # Mid-flight flip to ``0`` does NOT re-resolve.
    monkeypatch.setenv(ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED_ENV, "0")
    assert is_llm_judge_enabled() is True
    # Reset + re-resolve → now reflects the mutated env.
    reset_llm_judge_resolver_for_tests()
    assert is_llm_judge_enabled() is False


def test_reset_helper_clears_cache(monkeypatch):
    """``reset_llm_judge_resolver_for_tests`` lets tests re-resolve.

    Same pattern as ``reset_attestation_resolver_for_tests`` — the
    resolver MUST honor the test-only cache wipe so an outer env
    mutation takes effect on the next call.
    """
    monkeypatch.setenv(ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED_ENV, "0")
    reset_llm_judge_resolver_for_tests()
    assert is_llm_judge_enabled() is False
    monkeypatch.setenv(ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED_ENV, "1")
    reset_llm_judge_resolver_for_tests()
    assert is_llm_judge_enabled() is True

# ═════════════════════════════════════════════════════════════════════════════
# Wall-clock cap resolver — ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S
# ═════════════════════════════════════════════════════════════════════════════
#
# Operator tuning decision 2026-09-07 (default 25.0s, min clamp 5.0s;
# Pattern C sibling resolver — sibling to the boolean kill-switch above).
# Rationale grounded in the tester live-LLM probe: real quick-model
# latencies on 2026-09-07 showed successes 2.6s–13.6s with 4/8 calls
# >15s — the prior hardcoded 10.0s cap was firing on a substantial
# fraction of genuine-report calls and silently flipping the gate to
# the conservative deny+nudge path. The bump + env-tunability restores
# the feature's intended behavior.


# Valid parse inputs (above the clamp, any float-parseable).
_VALID_TIMEOUT_INPUTS = [
    "30",       # integer-string (most common operator shape)
    "15.5",     # explicit float
    "15.0",     # float with trailing zero — exact, not above-clamp
    "25",       # the default value, explicitly set
    "40",       # operator loosening
    "  20  ",   # whitespace-padded — strip() must engage
]


# Invalid parse inputs (non-numeric OR <= 0). All resolve to the
# default ``DEFAULT_JUDGE_TIMEOUT_S`` (25.0s) WITH a one-shot WARN.
_INVALID_TIMEOUT_INPUTS = [
    "abc",
    "1.5x",     # mixed numeric + alpha
    "",
    "-1",
    "-25",
    "0",
    "0.0",
]


# Below-clamp inputs (positive but < MIN_JUDGE_TIMEOUT_S = 5.0s).
# All resolve to the clamp floor WITH a one-shot WARN.
_BELOW_CLAMP_TIMEOUT_INPUTS = [
    "1",        # way below clamp
    "4.9",
    "3.0",
    "0.5",
    "  2  ",    # whitespace-padded variant
]


@pytest.mark.parametrize("raw", _VALID_TIMEOUT_INPUTS)
def test_parse_judge_timeout_s_valid_inputs_parse_as_floats(raw):
    """Valid floats (above the clamp) parse to the parsed value unchanged.

    The resolver is a numeric env; integer-strings (``"30"``) and
    explicit floats (``"15.5"``) are both accepted. Whitespace
    padding engages ``strip()`` before parse — ``"  20  "`` resolves
    to ``20.0`` not ``"  20  "`` as a string.
    """
    result = _parse_judge_timeout_s(_timeout_source(raw))
    # Convert the expected value via ``float()`` so the parametrized
    # literals (``"30"`` → ``30``) match the resolver's float output
    # (``30.0``). Exact equality on the float keeps the assertion
    # crisp; ``pytest.approx`` would mask a refactor that quietly
    # rounded the result.
    assert result == float(raw.strip())
    # The result is always above the clamp (these are valid-above-floor
    # inputs) — guard against a refactor that drops the clamp check.
    assert result > MIN_JUDGE_TIMEOUT_S


@pytest.mark.parametrize("raw", _BELOW_CLAMP_TIMEOUT_INPUTS)
def test_parse_judge_timeout_s_below_clamp_clamps_to_floor(raw):
    """Positive values below ``MIN_JUDGE_TIMEOUT_S`` clamp to the floor.

    The clamp protects against an operator setting a timeout too
    tight to surface a real chat completion call (a sub-5s call
    cannot realistically complete a round-trip including network
    jitter; clamping to e.g. 1s would make the timeout
    indistinguishable from an immediate provider error). The clamp
    WARN is the operator's audit trail.
    """
    assert _parse_judge_timeout_s(_timeout_source(raw)) == MIN_JUDGE_TIMEOUT_S


@pytest.mark.parametrize("raw", _INVALID_TIMEOUT_INPUTS)
def test_parse_judge_timeout_s_invalid_falls_open_to_default(raw):
    """Non-numeric / non-positive / blank values fail OPEN to the default.

    Mirrors the boolean kill-switch's FAIL-ON posture scaled to a
    numeric env: a typo'd ``"abc"`` or a ``"=0"`` would be the
    dangerous direction (always-timeout = silent gate-flip to
    conservative deny+nudge); fail-OPEN keeps the judge alive at
    the documented default 25.0s. Blank / unset is also default
    (the resolver handles both shapes identically — unset env and
    blank value).
    """
    assert _parse_judge_timeout_s(_timeout_source(raw)) == DEFAULT_JUDGE_TIMEOUT_S


def test_parse_judge_timeout_s_unset_returns_default():
    """Unset env (key absent from source) → :data:`DEFAULT_JUDGE_TIMEOUT_S` (25.0s).

    Source dict lacks the env key entirely — the resolver must not
    ``KeyError`` and must return the documented default.
    """
    assert _parse_judge_timeout_s({}) == DEFAULT_JUDGE_TIMEOUT_S == 25.0


def test_parse_judge_timeout_s_none_value_returns_default():
    """A ``None`` value (defensive — resolver handles ``None``) → default.

    Same defensive posture as the boolean kill-switch resolver
    (``test_parse_llm_judge_enabled_none_value_returns_default``):
    a None value (which can arise from a misspelled env that lands
    as None rather than as a string) resolves to the default, not
    a TypeError.
    """
    assert _parse_judge_timeout_s(
        {ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV: None}  # type: ignore[dict-item]
    ) == DEFAULT_JUDGE_TIMEOUT_S


# ─────────────────────────────────────────────────────────────────────────────
# One-shot WARN emission — invalid + below-clamp paths emit, valid + unset do not
# ─────────────────────────────────────────────────────────────────────────────


class TestParseJudgeTimeoutSWarnEmission:
    """One-shot WARN flag discipline — invalid + below-clamp WARN, valid silent.

    The two WARN buckets (invalid / below-clamp) use INDEPENDENT
    one-shot flags so an operator passing both an invalid AND a
    below-clamp value across env reload (rare, but conceivable)
    sees both messages instead of one masking the other.
    """

    def test_invalid_emits_warn(self, caplog):
        """Non-numeric env value emits one-shot WARN carrying the raw value."""
        with caplog.at_level(
            logging.WARNING,
            logger="daemon.services.attestation_judge_timeout_resolver",
        ):
            result = _parse_judge_timeout_s(_timeout_source("abc"))
        assert result == DEFAULT_JUDGE_TIMEOUT_S
        # Exactly one WARN; message carries the raw value + the default.
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warns) == 1
        assert "abc" in warns[0].getMessage()
        assert str(DEFAULT_JUDGE_TIMEOUT_S) in warns[0].getMessage()

    def test_below_clamp_emits_clamp_warn(self, caplog):
        """Below-clamp value emits one-shot clamp WARN carrying the raw value."""
        with caplog.at_level(
            logging.WARNING,
            logger="daemon.services.attestation_judge_timeout_resolver",
        ):
            result = _parse_judge_timeout_s(_timeout_source("2"))
        assert result == MIN_JUDGE_TIMEOUT_S
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warns) == 1
        assert "2" in warns[0].getMessage()
        assert "clamp" in warns[0].getMessage().lower()

    def test_zero_emits_invalid_warn_not_clamp_warn(self, caplog):
        """``=0`` is an INVALID value (not a clamp value) — fails OPEN to default.

        Zero would be a permanent-timeout — the dangerous direction
        (silent gate flip). The resolver treats ``<= 0`` as invalid
        (fail-OPEN to default), distinct from ``0 < v < clamp``
        which clamps to the floor.
        """
        with caplog.at_level(
            logging.WARNING,
            logger="daemon.services.attestation_judge_timeout_resolver",
        ):
            result = _parse_judge_timeout_s(_timeout_source("0"))
        assert result == DEFAULT_JUDGE_TIMEOUT_S

    def test_valid_emits_no_warn(self, caplog):
        """Valid parse (above clamp) emits NO WARN — clean operator config."""
        with caplog.at_level(
            logging.WARNING,
            logger="daemon.services.attestation_judge_timeout_resolver",
        ):
            result = _parse_judge_timeout_s(_timeout_source("30"))
        assert result == 30.0
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warns == []

    def test_unset_emits_no_warn(self, caplog):
        """Unset env emits NO WARN — the default is the documented baseline."""
        with caplog.at_level(
            logging.WARNING,
            logger="daemon.services.attestation_judge_timeout_resolver",
        ):
            result = _parse_judge_timeout_s({})
        assert result == DEFAULT_JUDGE_TIMEOUT_S
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warns == []


# ─────────────────────────────────────────────────────────────────────────────
# Cached-global semantics — restart-read, reset helper re-resolves
# ─────────────────────────────────────────────────────────────────────────────


def test_get_judge_timeout_s_caches_first_resolution(monkeypatch):
    """Cached-global wins over mid-flight env mutation (restart-read).

    Pattern C discipline (mirror of the boolean kill-switch test
    above): the first call resolves and caches; subsequent calls
    return the cached float even if the env has been mutated in
    the interim. Flip requires restart.
    """
    # First read with env unset → default 25.0.
    assert get_judge_timeout_s() == 25.0
    # Mid-flight flip to ``15`` does NOT re-resolve.
    monkeypatch.setenv(ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV, "15")
    assert get_judge_timeout_s() == 25.0
    # Reset + re-resolve → now reflects the mutated env.
    reset_judge_timeout_resolver_for_tests()
    assert get_judge_timeout_s() == 15.0


def test_reset_judge_timeout_resolver_for_tests_clears_cache(monkeypatch):
    """``reset_judge_timeout_resolver_for_tests`` lets tests re-resolve.

    Mirrors the boolean kill-switch reset test above. Tests mutate
    env vars THEN call this helper THEN call
    :func:`get_judge_timeout_s` to re-resolve under the new env.
    """
    monkeypatch.setenv(ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV, "15")
    reset_judge_timeout_resolver_for_tests()
    assert get_judge_timeout_s() == 15.0
    monkeypatch.setenv(ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV, "30")
    reset_judge_timeout_resolver_for_tests()
    assert get_judge_timeout_s() == 30.0


def test_reset_clears_one_shot_warn_flags(monkeypatch, caplog):
    """Reset clears the one-shot WARN flags so a new bad env re-emits.

    The reset helper MUST clear the one-shot flags (not just the
    cached value) so a test that resolves a bad env (emits WARN) and
    then resolves a clean env (silent) and then resolves a bad env
    again (must re-emit WARN) sees the expected behavior. This
    pins the reset helper's contract — a refactor that forgets to
    clear the flags would silently suppress the second WARN.
    """
    with caplog.at_level(
        logging.WARNING,
        logger="daemon.services.attestation_judge_timeout_resolver",
    ):
        # First bad resolution — emits one-shot WARN.
        assert _parse_judge_timeout_s(_timeout_source("abc")) == DEFAULT_JUDGE_TIMEOUT_S
        assert len(
            [r for r in caplog.records if r.levelno == logging.WARNING]
        ) == 1
    caplog.clear()
    # Reset clears the one-shot flags AND the cache.
    reset_judge_timeout_resolver_for_tests()
    with caplog.at_level(
        logging.WARNING,
        logger="daemon.services.attestation_judge_timeout_resolver",
    ):
        # Second bad resolution after reset — must re-emit WARN.
        assert _parse_judge_timeout_s(_timeout_source("xyz")) == DEFAULT_JUDGE_TIMEOUT_S
        assert len(
            [r for r in caplog.records if r.levelno == logging.WARNING]
        ) == 1


def test_sibling_resolver_caches_are_independent(monkeypatch):
    """Boolean kill-switch cache + timeout cache are independent.

    Resetting one MUST NOT clobber the other. A test that flips
    the timeout env while keeping the kill-switch env stable should
    see the timeout re-resolve AND the kill-switch stay cached.
    """
    # First call: both resolvers cache their defaults.
    assert is_llm_judge_enabled() is True
    assert get_judge_timeout_s() == 25.0
    # Reset ONLY the timeout cache.
    monkeypatch.setenv(ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV, "15")
    reset_judge_timeout_resolver_for_tests()
    # Kill-switch cache survives the timeout-only reset.
    monkeypatch.setenv(ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED_ENV, "0")
    assert is_llm_judge_enabled() is True  # still cached ON
    # Timeout cache re-resolves under the new env.
    assert get_judge_timeout_s() == 15.0
    # Now reset the kill-switch cache; timeout stays at 15.
    reset_llm_judge_resolver_for_tests()
    assert is_llm_judge_enabled() is False  # re-resolves to OFF
    assert get_judge_timeout_s() == 15.0  # still 15, no re-resolve
