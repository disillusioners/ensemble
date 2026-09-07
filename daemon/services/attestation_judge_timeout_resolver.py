"""Restart-read resolver for the LCA inline-LLM completion-report judge timeout.

Phase 6 fastfollow tuning (2026-09-07 — operator decision grounded in the
tester live-LLM probe captured at ``.agents/tester/RESULTS/2026-09-07-lca-judge-live-probe*``).
This module is the **SINGLE HOME** of the judge wall-clock cap env
resolution — sibling to :mod:`daemon.services.attestation_judge_resolver`
(the boolean kill-switch) and :mod:`daemon.services.attestation_resolver`
(the main tri-state mode resolver).

* ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S`` (default 25.0s;
  restart-read; minimum clamp 5.0s);
* the cached-global Pattern C resolver;
* the test-only cache-reset helper.

Pattern C shape — fail-open, one-shot WARN, restart-read
--------------------------------------------------------

Mirrors :mod:`daemon.services.attestation_judge_resolver` and
:mod:`daemon.services.attestation_resolver` (the established Pattern C
precedent in this codebase — ``daemon/services/instance_messaging.py:
114-191`` is the explicit WC-wake precedent this module mirrors).
The shape is: **module env resolver + cached global + one-shot WARN +
restart-required to flip**.

An invalid env value (non-numeric, negative, zero) emits a ONE-SHOT
WARN carrying the offending raw value + the resolved default, and falls
back to the default ``25.0s``. A value below the documented minimum
clamp ``5.0s`` emits a DIFFERENT one-shot WARN (clamp WARN) and clamps
the resolved value to ``5.0s``. The two WARN paths use independent
one-shot flags so an operator who passes both an invalid AND a
below-clamp value (impossible in one env line, but the WARN buckets are
independent anyway) sees both messages instead of one masking the other.

Rationale (tester live-LLM probe 2026-09-07, evidence commits
b42f7237..2a43904c on branch ``feature/leader-completion-attestation``):

* Real quick-model latencies (successes 2.6s–13.6s; 4/8 calls >15s on
  the 2026-09-07 probe) revealed the hardcoded ``JUDGE_TIMEOUT_S=10.0``
  as a false-positive amplifier — the judge was timing out
  ~50% of the genuine-report calls and forcing the gate into the
  conservative deny+nudge path, silently defeating the feature's
  purpose. The default was bumped from 10.0s → 25.0s and the env was
  exposed so operators can re-tune without a code change.
* The minimum clamp ``5.0s`` is the floor that still lets the judge
  surface a real timeout (a sub-5s call cannot realistically complete a
  chat completion round-trip including network jitter; clamping to
  e.g. 1s would make the timeout indistinguishable from an immediate
  provider error). The clamp WARN is the operator's audit trail.
* The trade-off: a longer worst-case turn-end wait on the rare deny
  path (the judge is only invoked on the WOULD-BE-DENY branch) vs
  fewer false nudges of genuine reports. The 25s default keeps the
  wait bounded while letting the quick-model tail latency ride.

Public API
----------

* :func:`get_judge_timeout_s` — the cached-global resolver;
* :func:`reset_judge_timeout_resolver_for_tests` — test-only cache reset.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Env var name (single source of truth)
# ─────────────────────────────────────────────────────────────────────────────

ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV = (
    "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S"
)


# ─────────────────────────────────────────────────────────────────────────────
# Defaults + bounds (single source of truth — referenced by tests + setup.md)
# ─────────────────────────────────────────────────────────────────────────────

#: Default ship posture for the judge wall-clock cap. Bumped from 10.0s →
#: 25.0s on 2026-09-07 (operator decision grounded in the tester live-LLM
#: probe; see module docstring rationale). Operators tighten via the env
#: var (e.g. ``=15`` for a faster tail-bound); restart is required (Pattern
#: C — no live flip). The default mirrors the quick-model tail latency
#: observed in the probe (successes 2.6–13.6s with 4/8 calls >15s).
DEFAULT_JUDGE_TIMEOUT_S: float = 25.0

#: Minimum clamp for the resolved timeout. Values below this clamp to
#: the floor with a one-shot WARN. The floor still lets a real timeout
#: surface (a sub-5s call cannot realistically complete a chat
#: completion round-trip including network jitter); clamping to e.g.
#: 1s would make the timeout indistinguishable from an immediate
#: provider error.
MIN_JUDGE_TIMEOUT_S: float = 5.0


# ─────────────────────────────────────────────────────────────────────────────
# Pattern C cached-global state — restart-read; flip requires restart
# ─────────────────────────────────────────────────────────────────────────────

_CACHED_JUDGE_TIMEOUT_S: float | None = None
#: One-shot WARN flag for invalid (non-numeric / negative / zero) values.
#: Emits on the first resolution that hits an invalid value; subsequent
#: resolutions are silent (the cached global makes invalid-env WARNs
#: idempotent within a process).
_INVALID_VALUE_WARN_EMITTED: bool = False
#: One-shot WARN flag for below-clamp values. Independent flag so an
#: operator passing both an invalid AND a below-clamp value (rare, but
#: conceivable across env reload + manual override) sees both messages
#: instead of one masking the other.
_BELOW_CLAMP_WARN_EMITTED: bool = False


def _parse_judge_timeout_s(source: dict[str, str]) -> float:
    """Parse the timeout env, fail-OPEN to the default, clamp to the minimum.

    Failure / clamp policy (fail-OPEN; mirrors the boolean kill-switch
    resolver's "silent default-ON" posture scaled to a numeric env):

    * Blank / unset → :data:`DEFAULT_JUDGE_TIMEOUT_S` (25.0s).
    * Non-numeric (including floats that cannot be parsed, e.g. "abc",
      "1.5x") → :data:`DEFAULT_JUDGE_TIMEOUT_S` + one-shot invalid-WARN.
    * ``<= 0`` → :data:`DEFAULT_JUDGE_TIMEOUT_S` + one-shot invalid-WARN
      (a zero / negative timeout is operationally equivalent to
      "always-timeout" which is the dangerous direction; fail-OPEN).
    * ``0 < value < MIN_JUDGE_TIMEOUT_S`` → :data:`MIN_JUDGE_TIMEOUT_S`
      + one-shot below-clamp WARN (the clamp protects against an
      operator setting a timeout too tight to surface a real call).
    * Otherwise → the parsed float (caller clamps via this function).
    """
    global _INVALID_VALUE_WARN_EMITTED, _BELOW_CLAMP_WARN_EMITTED
    raw = source.get(ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV, "")
    if raw is None:
        raw = ""
    raw = str(raw).strip()
    if not raw:
        return DEFAULT_JUDGE_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        if not _INVALID_VALUE_WARN_EMITTED:
            _INVALID_VALUE_WARN_EMITTED = True
            logger.warning(
                "%s=%r is not a valid float; failing OPEN to the "
                "default %.1fs. Restart required after fixing the "
                "env value.",
                ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV,
                raw,
                DEFAULT_JUDGE_TIMEOUT_S,
            )
        return DEFAULT_JUDGE_TIMEOUT_S
    if value <= 0:
        if not _INVALID_VALUE_WARN_EMITTED:
            _INVALID_VALUE_WARN_EMITTED = True
            logger.warning(
                "%s=%r is not a positive timeout; failing OPEN to the "
                "default %.1fs. Restart required after fixing the "
                "env value.",
                ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV,
                raw,
                DEFAULT_JUDGE_TIMEOUT_S,
            )
        return DEFAULT_JUDGE_TIMEOUT_S
    if value < MIN_JUDGE_TIMEOUT_S:
        if not _BELOW_CLAMP_WARN_EMITTED:
            _BELOW_CLAMP_WARN_EMITTED = True
            logger.warning(
                "%s=%r is below the minimum clamp %.1fs; clamping to "
                "%.1fs. Restart required after fixing the env value.",
                ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S_ENV,
                raw,
                MIN_JUDGE_TIMEOUT_S,
                MIN_JUDGE_TIMEOUT_S,
            )
        return MIN_JUDGE_TIMEOUT_S
    return value


def get_judge_timeout_s() -> float:
    """Return the cached wall-clock cap (Pattern C, restart-read).

    Returns the cached value (resolved once at first call; subsequent
    calls return the cached float — restart required to flip). Pattern
    C resolver; mirrors the WC-wake / governor-recursion /
    attestation-mode / judge-enabled resolvers' identity-preservation
    contract. Returns :data:`DEFAULT_JUDGE_TIMEOUT_S` (25.0s) on unset
    env, :data:`MIN_JUDGE_TIMEOUT_S` (5.0s) on below-clamp values.
    """
    global _CACHED_JUDGE_TIMEOUT_S
    if _CACHED_JUDGE_TIMEOUT_S is None:
        _CACHED_JUDGE_TIMEOUT_S = _parse_judge_timeout_s(os.environ)
    return _CACHED_JUDGE_TIMEOUT_S


def reset_judge_timeout_resolver_for_tests() -> None:
    """Clear the cached resolver + one-shot WARN flags.

    Test-only — production code never invokes this. Mutate env vars
    THEN call this helper THEN call :func:`get_judge_timeout_s` to
    re-resolve under the new env. Clears both one-shot flags so the
    new resolution can emit fresh WARNs if the new env still trips
    the invalid / below-clamp branches.
    """
    global _CACHED_JUDGE_TIMEOUT_S, _INVALID_VALUE_WARN_EMITTED
    global _BELOW_CLAMP_WARN_EMITTED
    _CACHED_JUDGE_TIMEOUT_S = None
    _INVALID_VALUE_WARN_EMITTED = False
    _BELOW_CLAMP_WARN_EMITTED = False
