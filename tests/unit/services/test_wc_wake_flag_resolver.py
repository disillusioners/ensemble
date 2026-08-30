"""Permanent unit tests for the WC-wake enqueue kill-switch resolver.

W2 (2026-08-30 pre-flip batch): the resolver's truthy tuple included
``""`` so BLANKING the env mid-incident (``ENSEMBLE_WC_WAKE_ENQUEUE=``)
resolved the routing pivot ON, defeating the OFF=instant-revert contract
(C2-D2.5-FLIP). These tests pin the full spelling contract:
blank / unknown / unset → OFF; 1/true/yes/on → ON; 0/false/no/off → OFF.

Unlike the governor-guard resolver (ON default, where the ``""`` truthy
match is consistent with its ``get(..., "1")`` unset default), the
WC-wake resolver defaults OFF (``get(..., "0")``), so a blank env MUST
resolve OFF — there is no unset path that reaches the truthy branch.
"""

from __future__ import annotations

import pytest

from daemon.services.instance_messaging import (
    _WC_WAKE_ENQUEUE_ENV,
    _reset_wc_wake_enqueue_for_tests,
    _resolve_wc_wake_enqueue_enabled,
)


@pytest.fixture(autouse=True)
def _isolate_env_and_cache(monkeypatch: pytest.MonkeyPatch):
    """Isolate the env var and clear the module-global cache per case."""
    monkeypatch.delenv(_WC_WAKE_ENQUEUE_ENV, raising=False)
    _reset_wc_wake_enqueue_for_tests()
    yield
    _reset_wc_wake_enqueue_for_tests()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(None, False, id="unset_off"),
        pytest.param("0", False, id="zero_off"),
        pytest.param("", False, id="blank_off"),
        pytest.param("false", False, id="false_off"),
        pytest.param("no", False, id="no_off"),
        pytest.param("off", False, id="off_off"),
        pytest.param("1", True, id="one_on"),
        pytest.param("true", True, id="true_on"),
        pytest.param("yes", True, id="yes_on"),
        pytest.param("on", True, id="on_on"),
        pytest.param("garbage", False, id="unknown_falls_back_off"),
        pytest.param(" 1 ", True, id="surrounding_whitespace_trimmed"),
        pytest.param("TRUE", True, id="case_insensitive_truthy"),
    ],
)
def test_resolver_spelling_contract(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: bool
):
    """Full truthy/falsy spelling contract; ``None`` = env left unset."""
    if raw is not None:
        monkeypatch.setenv(_WC_WAKE_ENQUEUE_ENV, raw)
    assert _resolve_wc_wake_enqueue_enabled() is expected


def test_blank_env_resolves_off_after_prior_on_resolution(
    monkeypatch: pytest.MonkeyPatch,
):
    """W2 regression core: blanking the env mid-incident reverts to OFF.

    Simulates the incident reflex: the pivot was flipped ON (env ``1``),
    the operator blanks the env and restarts — the fresh resolution MUST
    be OFF, not the cached/stale ON.
    """
    monkeypatch.setenv(_WC_WAKE_ENQUEUE_ENV, "1")
    assert _resolve_wc_wake_enqueue_enabled() is True

    monkeypatch.setenv(_WC_WAKE_ENQUEUE_ENV, "")
    _reset_wc_wake_enqueue_for_tests()
    assert _resolve_wc_wake_enqueue_enabled() is False


def test_resolver_caches_first_resolution_until_reset(
    monkeypatch: pytest.MonkeyPatch,
):
    """Restart-required semantics: mid-flight env flips do NOT re-resolve."""
    monkeypatch.setenv(_WC_WAKE_ENQUEUE_ENV, "1")
    assert _resolve_wc_wake_enqueue_enabled() is True

    monkeypatch.setenv(_WC_WAKE_ENQUEUE_ENV, "0")
    assert _resolve_wc_wake_enqueue_enabled() is True

    _reset_wc_wake_enqueue_for_tests()
    assert _resolve_wc_wake_enqueue_enabled() is False
