"""Restart-read resolver for the POK write-gate judge timeout budget.

Phase 7, task T7.10 (pok-write-gate plan set:
``.agents/shared/planning/pok-write-gate/phase7-plan.md``; binding
picks in ``design-amendments-v2.md`` §13.1 D1 OVERTURNED), as amended
by the Emperor's 2026-11-25 five-minute ruling: the TOTAL judge
budget is **300s** and BOTH the per-attempt cap and the TOTAL are
env-tunable; the retry budget is untouched.

This module is the **SINGLE HOME** of the gate judge's timeout
resolution — the sibling of
:mod:`daemon.services.attestation_judge_timeout_resolver` (the
established Pattern C precedent in this codebase). It owns:

* ``ENSEMBLE_POK_GATE_TIMEOUT_S`` — the PER-ATTEMPT wall-clock cap of
  one judge call (default ``300.0``s, min clamp ``10.0``s, max clamp
  ``360.0``s);
* ``ENSEMBLE_POK_GATE_WALL_CLOCK_S`` — the TOTAL wall clock for the
  whole judge cycle (default ``300.0``s, min clamp ``30.0``s, max
  clamp ``1800.0``s);
* the cached-global Pattern C resolvers;
* the test-only cache-reset helper.

Pattern C shape — fail-open, one-shot WARN, restart-read
--------------------------------------------------------

Mirrors :mod:`daemon.services.attestation_judge_timeout_resolver`:
**module env resolver + cached global + one-shot WARN +
restart-required to flip**. An invalid env value (non-numeric,
negative, zero) emits a ONE-SHOT WARN carrying the offending raw
value + the resolved default and fails OPEN to the default. A value
below the knob's minimum clamp emits a DIFFERENT one-shot WARN
(clamp WARN) and clamps UP; a value above the knob's maximum clamps
DOWN (max WARN). The WARN buckets are independent — per knob AND
per direction — so an operator who trips several buckets sees every
message instead of one masking another.

Why the defaults are what they are
----------------------------------

D1 was OVERTURNED on evidence (design-amendments-v2 §13.1): the old
``12s``-per-attempt figure sat BELOW an observed ``13.6s`` judge
SUCCESS (the 2026-09-07 live-quick probe measured successes in the
``2.6s–13.6s`` band with 4 of 8 calls above 15s), so it would have
fail-closed calls proven to succeed. The Emperor's 2026-11-25
ruling raised the budget again for the same class of reason — the
``45s`` TOTAL fail-closed judge rulings that simply take minutes —
to the new shape:

* ``300s`` per attempt — EQUAL to the TOTAL, so one judge call may
  use the WHOLE budget and the per-attempt value never silently
  truncates a ruling (the gate spends ``min(attempt, remaining)``);
* at most ``1`` retry (so at most TWO attempts) — the ruling changed
  the BUDGET, not the retry policy;
* a ``300s`` TOTAL wall clock for the whole judge cycle, now
  env-tunable via ``ENSEMBLE_POK_GATE_WALL_CLOCK_S`` (it was a
  constant before the ruling; it lives here so both knobs are read
  through the same seam).

The per-attempt max clamp rose ``180.0`` → ``360.0`` because the old
``180s`` clamp would have SILENTLY cut the new ``300s`` per-attempt
default down to ``180`` and defeated the ruling; ``360s`` keeps the
ceiling tight above the default. The TOTAL clamps to
``[30.0, 1800.0]``: the floor keeps a judge cycle distinguishable
from an immediate failure, and the ceiling is the fat-finger guard
justified below.

Coherence with the daemon's own ceilings (the corrected invariant)
------------------------------------------------------------------

The pre-ruling docstring claimed the ``45s`` wall cap "keeps the
gate inside the 180s graph heartbeat". IMPRECISE — corrected: the
``180s`` figure is a LOG-sumup window in ``daemon/graph.py`` (an
INFO heartbeat LINE — ``[LLM] heartbeat 180s`` via
``daemon.services.log_sumup``), NOT a kill; nothing enforces a 180s
bound on a tool call. The real ceiling above a native tool call is
``graph_timeout_minutes`` (``daemon/config.py`` ServicesConfig,
default ``120.0`` min = ``7200s``), enforced by MainLoopBridge as
the whole-turn hard cap. The worker heartbeat (``TaskHeartbeat``,
30s) is a separate daemon THREAD and a sync tool runs via langchain's
``run_in_executor``, so a multi-minute judge call neither starves
the event loop nor trips the heartbeat. The binding invariant is::

    resolved wall cap < graph_timeout_minutes * 60  (default 7200s)

and the ``1800s`` max clamp guarantees it with room to spare: even a
maxed-out wall leaves ≥ ``5400s`` of the turn budget for everything
else the turn does.

Public API
----------

* :func:`get_gate_timeout_s` — the cached-global per-attempt resolver;
* :func:`get_gate_wall_clock_cap_s` — the cached-global TOTAL budget
  resolver (env-tunable since the 2026-11-25 ruling);
* :func:`get_gate_max_retries` — the retry budget constant;
* :func:`reset_gate_timeout_resolver_for_tests` — test-only cache reset.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Env var names (single source of truth)
# ─────────────────────────────────────────────────────────────────────────────

POK_GATE_TIMEOUT_S_ENV = "ENSEMBLE_POK_GATE_TIMEOUT_S"
POK_GATE_WALL_CLOCK_S_ENV = "ENSEMBLE_POK_GATE_WALL_CLOCK_S"


# ─────────────────────────────────────────────────────────────────────────────
# Defaults + bounds (single source of truth — referenced by tests + the gate)
# ─────────────────────────────────────────────────────────────────────────────

#: PER-ATTEMPT wall-clock cap for one judge call. Since the Emperor's
#: 2026-11-25 ruling it EQUALS the TOTAL budget: one judge call may use
#: the whole five minutes, so a long-but-legitimate ruling is never
#: truncated by its own per-attempt slice.
DEFAULT_GATE_TIMEOUT_S: float = 300.0

#: Minimum clamp for the resolved per-attempt timeout. Below this the
#: timeout becomes indistinguishable from an immediate provider error
#: (the attestation judge's floor-rationale, scaled).
MIN_GATE_TIMEOUT_S: float = 10.0

#: Maximum clamp for the resolved per-attempt timeout. Raised
#: ``180.0`` → ``360.0`` with the five-minute ruling: the old ``180s``
#: clamp would have silently cut the new ``300s`` default to ``180``.
#: ``360s`` stays tight above the default; the TOTAL wall still binds
#: first whenever the per-attempt value exceeds the remaining budget
#: (the gate spends ``min(attempt, remaining)``).
MAX_GATE_TIMEOUT_S: float = 360.0

#: TOTAL wall clock for the whole judge cycle (all attempts + the
#: retry) — the Emperor's 2026-11-25 five-minute ruling. Env-tunable
#: via :data:`POK_GATE_WALL_CLOCK_S_ENV`; this is the DEFAULT the
#: resolver fails open to.
DEFAULT_GATE_WALL_CLOCK_S: float = 300.0

#: Pre-ruling name of the TOTAL default (``GATE_WALL_CLOCK_CAP_S`` was
#: the constant itself before the ruling). Kept exported — the gate
#: imports it and the unit pins assert it. Same float as
#: :data:`DEFAULT_GATE_WALL_CLOCK_S`.
GATE_WALL_CLOCK_CAP_S: float = DEFAULT_GATE_WALL_CLOCK_S

#: Minimum clamp for the resolved TOTAL. Below ~one per-attempt floor
#: plus retry slack a judge cycle is indistinguishable from an
#: immediate failure — the floor keeps the knob from re-introducing
#: the very fail-closed truncation the ruling removes.
MIN_GATE_WALL_CLOCK_S: float = 30.0

#: Maximum clamp for the resolved TOTAL — the fat-finger guard. Must
#: (a) keep ``300`` reachable (it is, strictly inside) and (b) never
#: park a judge call beyond the daemon's own turn budget
#: (``graph_timeout_minutes`` * 60 = ``7200s``): a maxed-out wall
#: still leaves ≥ ``5400s`` for everything else the turn does.
MAX_GATE_WALL_CLOCK_S: float = 1800.0

#: The retry budget: AT MOST ONE retry, so at most TWO attempts. The
#: retry fires on a timeout or an unparsable verdict (the attestation
#: judge's retry-once semantics); a plain provider error keeps no
#: retry. UNCHANGED by the 2026-11-25 ruling (a budget change, not a
#: retry change).
GATE_MAX_RETRIES: int = 1


# ─────────────────────────────────────────────────────────────────────────────
# Pattern C cached-global state — restart-read; flip requires restart
# ─────────────────────────────────────────────────────────────────────────────

_CACHED_GATE_TIMEOUT_S: float | None = None
#: Cached TOTAL (env-tunable since the 2026-11-25 ruling).
_CACHED_GATE_WALL_CLOCK_S: float | None = None

#: One-shot WARN buckets — key format ``<knob>.<direction>``. Keys are
#: independent on purpose: an operator who trips an invalid value AND
#: a clamp on the same knob (or on both knobs) sees every message
#: instead of one masking another.
_WARN_BUCKETS: dict[str, bool] = {
    "timeout.invalid": False,
    "timeout.below": False,
    "timeout.above": False,
    "wall.invalid": False,
    "wall.below": False,
    "wall.above": False,
}


def _warn_once(bucket: str, message: str) -> None:
    """Emit ``message`` at WARN level exactly ONCE per ``bucket``."""
    if not _WARN_BUCKETS[bucket]:
        _WARN_BUCKETS[bucket] = True
        logger.warning(message)


def _parse_pattern_c_seconds(
    source: dict[str, str],
    *,
    env_name: str,
    default: float,
    min_bound: float,
    max_bound: float,
    invalid_bucket: str,
    below_bucket: str,
    above_bucket: str,
    above_note: str,
) -> float:
    """Parse one numeric timeout env — fail-OPEN, clamp, one-shot WARN.

    Shared Pattern C numeric core for BOTH knobs (the per-attempt cap
    and the TOTAL). Failure / clamp policy (fail-OPEN; the Pattern C
    numeric posture):

    * Blank / unset → ``default``.
    * Non-numeric → ``default`` + one-shot invalid-WARN.
    * ``<= 0`` → ``default`` + one-shot invalid-WARN (a zero /
      negative timeout is operationally equivalent to
      "always-timeout" — the dangerous direction; fail-OPEN).
    * ``0 < value < min_bound`` → ``min_bound`` + one-shot
      below-clamp WARN.
    * ``value > max_bound`` → ``max_bound`` + one-shot above-clamp
      WARN (``above_note`` carries the knob-specific why).
    * Otherwise → the parsed float.
    """
    raw = source.get(env_name, "")
    if raw is None:
        raw = ""
    raw = str(raw).strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        _warn_once(
            invalid_bucket,
            f"{env_name}={raw!r} is not a valid float; failing OPEN to "
            f"the default {default:.1f}s. Restart required after fixing "
            "the env value.",
        )
        return default
    if value <= 0:
        _warn_once(
            invalid_bucket,
            f"{env_name}={raw!r} is not a positive timeout; failing OPEN "
            f"to the default {default:.1f}s. Restart required after "
            "fixing the env value.",
        )
        return default
    if value < min_bound:
        _warn_once(
            below_bucket,
            f"{env_name}={raw!r} is below the minimum clamp "
            f"{min_bound:.1f}s; clamping to {min_bound:.1f}s. Restart "
            "required after fixing the env value.",
        )
        return min_bound
    if value > max_bound:
        _warn_once(
            above_bucket,
            f"{env_name}={raw!r} is above the maximum clamp "
            f"{max_bound:.1f}s; clamping to {max_bound:.1f}s. "
            f"{above_note} Restart required after fixing the env value.",
        )
        return max_bound
    return value


def _parse_gate_timeout_s(source: dict[str, str]) -> float:
    """Parse the per-attempt env (``ENSEMBLE_POK_GATE_TIMEOUT_S``).

    Default :data:`DEFAULT_GATE_TIMEOUT_S` (300.0s), clamped to
    ``[10.0, 360.0]``.
    """
    return _parse_pattern_c_seconds(
        source,
        env_name=POK_GATE_TIMEOUT_S_ENV,
        default=DEFAULT_GATE_TIMEOUT_S,
        min_bound=MIN_GATE_TIMEOUT_S,
        max_bound=MAX_GATE_TIMEOUT_S,
        invalid_bucket="timeout.invalid",
        below_bucket="timeout.below",
        above_bucket="timeout.above",
        above_note=(
            "The TOTAL wall clock binds first whenever this exceeds the "
            "remaining budget (the gate spends min(attempt, remaining))."
        ),
    )


def _parse_gate_wall_clock_s(source: dict[str, str]) -> float:
    """Parse the TOTAL env (``ENSEMBLE_POK_GATE_WALL_CLOCK_S``).

    Default :data:`DEFAULT_GATE_WALL_CLOCK_S` (300.0s), clamped to
    ``[30.0, 1800.0]`` — the ceiling keeps a fat-fingered value far
    inside the daemon turn budget (``graph_timeout_minutes`` * 60,
    default 7200s).
    """
    return _parse_pattern_c_seconds(
        source,
        env_name=POK_GATE_WALL_CLOCK_S_ENV,
        default=DEFAULT_GATE_WALL_CLOCK_S,
        min_bound=MIN_GATE_WALL_CLOCK_S,
        max_bound=MAX_GATE_WALL_CLOCK_S,
        invalid_bucket="wall.invalid",
        below_bucket="wall.below",
        above_bucket="wall.above",
        above_note=(
            "A judge cycle must stay far inside the daemon turn budget "
            "(graph_timeout_minutes*60, default 7200s)."
        ),
    )


def get_gate_timeout_s() -> float:
    """Return the cached PER-ATTEMPT wall-clock cap (Pattern C, restart-read).

    Resolved once at first call; subsequent calls return the cached
    float — a restart is required to flip it. Returns
    :data:`DEFAULT_GATE_TIMEOUT_S` (300.0s) on unset env,
    :data:`MIN_GATE_TIMEOUT_S` (10.0s) on below-clamp values and
    :data:`MAX_GATE_TIMEOUT_S` (360.0s) on above-clamp values.
    """
    global _CACHED_GATE_TIMEOUT_S
    if _CACHED_GATE_TIMEOUT_S is None:
        _CACHED_GATE_TIMEOUT_S = _parse_gate_timeout_s(os.environ)
    return _CACHED_GATE_TIMEOUT_S


def get_gate_wall_clock_cap_s() -> float:
    """The TOTAL judge budget (Pattern C, restart-read — env-tunable).

    Honours ``ENSEMBLE_POK_GATE_WALL_CLOCK_S`` on the next resolve;
    default :data:`DEFAULT_GATE_WALL_CLOCK_S` (300.0s — the Emperor's
    2026-11-25 five-minute ruling; it was the constant ``45.0``
    before), clamped to ``[30.0, 1800.0]``. The consumer spends it as
    a hard deadline (``monotonic() + cap``) and hands each attempt
    ``min(per_attempt, remaining)`` — so this TOTAL, not the
    per-attempt knob, is what bounds the whole cycle.
    """
    global _CACHED_GATE_WALL_CLOCK_S
    if _CACHED_GATE_WALL_CLOCK_S is None:
        _CACHED_GATE_WALL_CLOCK_S = _parse_gate_wall_clock_s(os.environ)
    return _CACHED_GATE_WALL_CLOCK_S


def get_gate_max_retries() -> int:
    """The retry budget (constant — at most ONE retry, two attempts)."""
    return GATE_MAX_RETRIES


def reset_gate_timeout_resolver_for_tests() -> None:
    """Clear the cached resolvers + every one-shot WARN bucket.

    Test-only — production code never invokes this. Mutate the env
    var THEN call this helper THEN call :func:`get_gate_timeout_s` /
    :func:`get_gate_wall_clock_cap_s` to re-resolve under the new env.
    """
    global _CACHED_GATE_TIMEOUT_S, _CACHED_GATE_WALL_CLOCK_S
    _CACHED_GATE_TIMEOUT_S = None
    _CACHED_GATE_WALL_CLOCK_S = None
    for bucket in _WARN_BUCKETS:
        _WARN_BUCKETS[bucket] = False
