"""Unit tests for the LCA inline-LLM judge kill-switch resolver.

Phase 6 fastfollow (2026-09-07) of the leader completion attestation
feature. Covers ``daemon/services/attestation_judge_resolver.py``:

* ``_parse_llm_judge_enabled`` truth table — the canonical boolean
  parse that the Pattern C cached-global :func:`is_llm_judge_enabled`
  delegates to. The boolean-shape env has four accepted falsy
  spellings (``0`` / ``false`` / ``no`` / ``off``); everything else
  resolves to ``True`` (Pattern C FAIL-ON / default-ON posture —
  typo'd ``yes-pls`` stays ON rather than tripping a silent disable).
* restart-read semantics — the cached-global resolver wins over
  mid-flight env mutation; the test-only reset helper re-resolves.
"""
from __future__ import annotations

import pytest

from daemon.services.attestation_judge_resolver import (
    DEFAULT_LLM_JUDGE_ENABLED,
    ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED_ENV,
    _LLM_JUDGE_FALSY,
    _parse_llm_judge_enabled,
    is_llm_judge_enabled,
    reset_llm_judge_resolver_for_tests,
)


# ─────────────────────────────────────────────────────────────────────────────
# Test fixtures + helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_judge_kill_switch(monkeypatch):
    """Hermetic kill-switch isolation per the W2 punch-list.

    Mirrors the autouse fixture in
    ``tests/unit/test_attestation_judge_wiring.py`` — clears the
    env var and resets the cached resolver around every test so an
    outer ``.env`` / CI mutation cannot leak in and silently
    disable the judge for the assertions below.
    """
    monkeypatch.delenv(
        ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED_ENV, raising=False
    )
    reset_llm_judge_resolver_for_tests()
    yield
    reset_llm_judge_resolver_for_tests()


def _source(value: str) -> dict[str, str]:
    """Build a one-key source dict for ``_parse_llm_judge_enabled``."""
    return {ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED_ENV: value}


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