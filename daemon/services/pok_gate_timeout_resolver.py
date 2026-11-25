"""Restart-read resolver for the POK write-gate judge timeout.

Phase 7, task T7.10 (pok-write-gate plan set:
``.agents/shared/planning/pok-write-gate/phase7-plan.md``; binding
picks in ``design-amendments-v2.md`` §13.1 D1 OVERTURNED).

This module is the **SINGLE HOME** of the gate judge's timeout
resolution — the sibling of
:mod:`daemon.services.attestation_judge_timeout_resolver` (the
established Pattern C precedent in this codebase). It owns:

* ``ENSEMBLE_POK_GATE_TIMEOUT_S`` — the PER-ATTEMPT wall-clock cap of
  one judge call (default ``20.0``s, min clamp ``10.0``s, max clamp
  ``180.0``s);
* the cached-global Pattern C resolver;
* the test-only cache-reset helper.

Pattern C shape — fail-open, one-shot WARN, restart-read
--------------------------------------------------------

Mirrors :mod:`daemon.services.attestation_judge_timeout_resolver`:
**module env resolver + cached global + one-shot WARN +
restart-required to flip**. An invalid env value (non-numeric,
negative, zero) emits a ONE-SOT WARN carrying the offending raw value
+ the resolved default and falls back to the default ``20.0``s. A
value below the documented minimum clamp ``10.0``s emits a DIFFERENT
one-shot WARN (clamp WARN) and clamps to ``10.0``s; a value above the
maximum ``180.0``s clamps to ``180.0``s (max WARN). The WARN paths
use independent one-shot flags so an operator who trips two buckets
sees both messages instead of one masking the other.

Why the defaults are what they are
----------------------------------

D1 was OVERTURNED on evidence (design-amendments-v2 §13.1): the old
``12s``-per-attempt figure sat BELOW an observed ``13.6s`` judge
SUCCESS (the 2026-09-07 live-quick probe measured successes in the
``2.6s–13.6s`` band with 4 of 8 calls above 15s), so it would have
fail-closed calls proven to succeed. The new shape is:

* ``20s`` per attempt,
* at most ``1`` retry (so at most TWO attempts),
* a ``45s`` TOTAL wall clock for the whole judge cycle.

The ``45s`` wall clock is the binding defense and is a CONSTANT (it
lives here so the per-attempt knob and the wall cap are read
together): a per-attempt value above ``~22s`` therefore buys nothing
beyond the wall — the wall fires first. That is deliberate: the wall
is what keeps the gate inside the 180s graph heartbeat even when an
operator mis-tunes the per-attempt knob.

Public API
----------

* :func:`get_gate_timeout_s` — the cached-global per-attempt resolver;
* :func:`get_gate_wall_clock_cap_s` — the TOTAL wall clock constant;
* :func:`get_gate_max_retries` — the retry budget constant;
* :func:`reset_gate_timeout_resolver_for_tests` — test-only cache reset.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Env var name (single source of truth)
# ─────────────────────────────────────────────────────────────────────────────

POK_GATE_TIMEOUT_S_ENV = "ENSEMBLE_POK_GATE_TIMEOUT_S"


# ─────────────────────────────────────────────────────────────────────────────
# Defaults + bounds (single source of truth — referenced by tests + the gate)
# ─────────────────────────────────────────────────────────────────────────────

#: PER-ATTEMPT wall-clock cap for one judge call. The D1-OVERTURNED
#: default (design-amendments-v2 §13.1): 20s — above the observed
#: 13.6s quick-judge success band, so a proven-good call is not
#: fail-closed by a too-tight cap.
DEFAULT_GATE_TIMEOUT_S: float = 20.0

#: Minimum clamp for the resolved per-attempt timeout. Below this the
#: timeout becomes indistinguishable from an immediate provider error
#: (the attestation judge's floor-rationale, scaled).
MIN_GATE_TIMEOUT_S: float = 10.0

#: Maximum clamp for the resolved per-attempt timeout. The wall clock
#: still binds first (see :data:`GATE_WALL_CLOCK_CAP_S`); the ceiling
#: exists so a fat-fingered env value cannot park a judge call for
#: minutes.
MAX_GATE_TIMEOUT_S: float = 180.0

#: TOTAL wall clock for the whole judge cycle (all attempts + the
#: retry). The binding defense: 45s sits inside the 180s graph
#: heartbeat (design-amendments-v2 §13.1).
GATE_WALL_CLOCK_CAP_S: float = 45.0

#: The retry budget: AT MOST ONE retry, so at most TWO attempts. The
#: retry fires on a timeout or an unparsable verdict (the attestation
#: judge's retry-once semantics); a plain provider error keeps no
#: retry.
GATE_MAX_RETRIES: int = 1


# ─────────────────────────────────────────────────────────────────────────────
# Pattern C cached-global state — restart-read; flip requires restart
# ─────────────────────────────────────────────────────────────────────────────

_CACHED_GATE_TIMEOUT_S: float | None = None
#: One-shot WARN flag for invalid (non-numeric / negative / zero) values.
_INVALID_VALUE_WARN_EMITTED: bool = False
#: One-shot WARN flag for below-clamp values. Independent flag so an
#: operator passing both an invalid AND a below-clamp value sees both
#: messages instead of one masking the other.
_BELOW_CLAMP_WARN_EMITTED: bool = False
#: One-shot WARN flag for above-clamp values (independent bucket).
_ABOVE_CLAMP_WARN_EMITTED: bool = False


def _parse_gate_timeout_s(source: dict[str, str]) -> float:
    """Parse the timeout env, fail-OPEN to the default, clamp to the bounds.

    Failure / clamp policy (fail-OPEN; the Pattern C numeric posture):

    * Blank / unset → :data:`DEFAULT_GATE_TIMEOUT_S` (20.0s).
    * Non-numeric → :data:`DEFAULT_GATE_TIMEOUT_S` + one-shot invalid-WARN.
    * ``<= 0`` → :data:`DEFAULT_GATE_TIMEOUT_S` + one-shot invalid-WARN
      (a zero / negative timeout is operationally equivalent to
      "always-timeout" — the dangerous direction; fail-OPEN).
    * ``0 < value < MIN_GATE_TIMEOUT_S`` → :data:`MIN_GATE_TIMEOUT_S`
      + one-shot below-clamp WARN.
    * ``value > MAX_GATE_TIMEOUT_S`` → :data:`MAX_GATE_TIMEOUT_S`
      + one-shot above-clamp WARN.
    * Otherwise → the parsed float.
    """
    global _INVALID_VALUE_WARN_EMITTED, _BELOW_CLAMP_WARN_EMITTED
    global _ABOVE_CLAMP_WARN_EMITTED
    raw = source.get(POK_GATE_TIMEOUT_S_ENV, "")
    if raw is None:
        raw = ""
    raw = str(raw).strip()
    if not raw:
        return DEFAULT_GATE_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        if not _INVALID_VALUE_WARN_EMITTED:
            _INVALID_VALUE_WARN_EMITTED = True
            logger.warning(
                "%s=%r is not a valid float; failing OPEN to the "
                "default %.1fs. Restart required after fixing the "
                "env value.",
                POK_GATE_TIMEOUT_S_ENV,
                raw,
                DEFAULT_GATE_TIMEOUT_S,
            )
        return DEFAULT_GATE_TIMEOUT_S
    if value <= 0:
        if not _INVALID_VALUE_WARN_EMITTED:
            _INVALID_VALUE_WARN_EMITTED = True
            logger.warning(
                "%s=%r is not a positive timeout; failing OPEN to the "
                "default %.1fs. Restart required after fixing the "
                "env value.",
                POK_GATE_TIMEOUT_S_ENV,
                raw,
                DEFAULT_GATE_TIMEOUT_S,
            )
        return DEFAULT_GATE_TIMEOUT_S
    if value < MIN_GATE_TIMEOUT_S:
        if not _BELOW_CLAMP_WARN_EMITTED:
            _BELOW_CLAMP_WARN_EMITTED = True
            logger.warning(
                "%s=%r is below the minimum clamp %.1fs; clamping to "
                "%.1fs. Restart required after fixing the env value.",
                POK_GATE_TIMEOUT_S_ENV,
                raw,
                MIN_GATE_TIMEOUT_S,
                MIN_GATE_TIMEOUT_S,
            )
        return MIN_GATE_TIMEOUT_S
    if value > MAX_GATE_TIMEOUT_S:
        if not _ABOVE_CLAMP_WARN_EMITTED:
            _ABOVE_CLAMP_WARN_EMITTED = True
            logger.warning(
                "%s=%r is above the maximum clamp %.1fs; clamping to "
                "%.1fs. The %.1fs wall clock binds first. Restart "
                "required after fixing the env value.",
                POK_GATE_TIMEOUT_S_ENV,
                raw,
                MAX_GATE_TIMEOUT_S,
                MAX_GATE_TIMEOUT_S,
                GATE_WALL_CLOCK_CAP_S,
            )
        return MAX_GATE_TIMEOUT_S
    return value


def get_gate_timeout_s() -> float:
    """Return the cached PER-ATTEMPT wall-clock cap (Pattern C, restart-read).

    Resolved once at first call; subsequent calls return the cached
    float — a restart is required to flip it. Returns
    :data:`DEFAULT_GATE_TIMEOUT_S` (20.0s) on unset env,
    :data:`MIN_GATE_TIMEOUT_S` (10.0s) on below-clamp values and
    :data:`MAX_GATE_TIMEOUT_S` (180.0s) on above-clamp values.
    """
    global _CACHED_GATE_TIMEOUT_S
    if _CACHED_GATE_TIMEOUT_S is None:
        _CACHED_GATE_TIMEOUT_S = _parse_gate_timeout_s(os.environ)
    return _CACHED_GATE_TIMEOUT_S


def get_gate_wall_clock_cap_s() -> float:
    """The TOTAL wall clock for the judge cycle (constant — 45s).

    Not env-tunable by design: it is the defense that keeps the gate
    inside the 180s graph heartbeat no matter how the per-attempt
    knob is tuned.
    """
    return GATE_WALL_CLOCK_CAP_S


def get_gate_max_retries() -> int:
    """The retry budget (constant — at most ONE retry, two attempts)."""
    return GATE_MAX_RETRIES


def reset_gate_timeout_resolver_for_tests() -> None:
    """Clear the cached resolver + the one-shot WARN flags.

    Test-only — production code never invokes this. Mutate the env
    var THEN call this helper THEN call :func:`get_gate_timeout_s` to
    re-resolve under the new env.
    """
    global _CACHED_GATE_TIMEOUT_S, _INVALID_VALUE_WARN_EMITTED
    global _BELOW_CLAMP_WARN_EMITTED, _ABOVE_CLAMP_WARN_EMITTED
    _CACHED_GATE_TIMEOUT_S = None
    _INVALID_VALUE_WARN_EMITTED = False
    _BELOW_CLAMP_WARN_EMITTED = False
    _ABOVE_CLAMP_WARN_EMITTED = False
