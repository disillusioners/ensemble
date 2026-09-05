"""Canonical tri-state mode resolver for the leader completion attestation gate.

Phase 4 of the leader completion attestation feature (``feature/leader-
completion-attestation``, tasks 4.1 / 4.2). This module is the **SINGLE
HOME** of:

* the tri-state mode env resolver (``ENSEMBLE_LEADER_ATTESTATION_MODE``);
* the two integer knobs (``ENSEMBLE_LEADER_ATTESTATION_WINDOW``,
  ``ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND``);
* the cached-global Pattern C resolver (restart-read; flip requires restart);
* the one-time boot log line announcing the resolved effective values;
* the O1 boot assert ``WINDOW <= min_recent_window`` (WARN-only, never
  fail-closed);
* the three CANONICAL promotion-metric counters (CANONICAL names per
  requirements.md / phase4-plan.md task 4.6):
  ``dry_log_total``, ``dry_log_deny_predicate_total``, ``enforce_denied_total``.

Resolver shape — Pattern C (mirrors the WC-wake resolver)
---------------------------------------------------------

Pattern C is the established kill-switch convention in this codebase
(``daemon/services/instance_messaging.py:114-191`` — WC-wake enqueue
routing pivot, the explicit precedent this module mirrors). The shape
is: **module env resolver + cached global + one-time boot log + WARN
on invalid value (fail-OPEN)**. The daemon main path reads the resolver
once at boot via :func:`emit_attestation_boot_log` and the gate reads
the same cached value on every evaluation via :func:`get_config`. Restart
is required to flip any of the three env vars (per C-2 / C-8 closed-by-
user constraints, ``decisions.md``).

Ruling 4 (FAIL-OPEN posture, SUPERSEDES plan prose)
---------------------------------------------------

An invalid env value MUST NOT disable the gate nor crash the boot — the
resolver logs a one-shot WARN and falls back to the default
(``mode=dry``, ``window=3``, ``deny_bound=3``). The ``fail-closed``
branch the plan task 4.1 test-notes mention as Pattern-A-only is **dead
code** for this feature — Pattern C is chosen, fail-OPEN is mandated.
This mirrors the WC-wake resolver's behavior (which fails OPEN with a
WARN for typo'd values per its resolver shape — the explicit precedent
the requirements.md AC-7.9 false fail-closed claim was CORRECTED to
mirror).

O1 boot assert (``WINDOW <= min_recent_window``)
------------------------------------------------

At boot time the resolver compares ``window`` against
``daemon.constants.MIN_RECENT_WINDOW`` (the compaction floor; default 3).
If ``window > MIN_RECENT_WINDOW``, the boot log line carries a ``WARN``
prefix and an explanatory message naming the risk
(``attestation_denied_count risk: WINDOW > min_recent_window; aggressive
context pressure may fold the attestation tool_call``). The assert is
**WARN-only** — the gate continues running with the configured WINDOW;
hard-fail would block Phase 2 deployment for misconfigured WINDOW
values, which is operationally brittle (architecture-recommendation.md
§G14 RESOLVED → FR-7 / AC-7.8). The default ``WINDOW=3`` matches the
default ``MIN_RECENT_WINDOW=3``, so the WARN does not fire under
default config.

Public API
----------

* :class:`AttestationConfig` — the dataclass returned by :func:`get_config`
  (mode/window/deny_bound/attestation_enabled/scope_applicable);
* :func:`get_config` — the cached-global resolver;
* :func:`emit_attestation_boot_log` — the one-time boot-log emitter;
* :func:`record_promotion_metric` — the canonical promotion-metric
  increment (``dry_log_total`` / ``dry_log_deny_predicate_total`` /
  ``enforce_denied_total``);
* :func:`reset_attestation_resolver_for_tests` — test-only cache reset.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal, TypedDict

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Env var names (single source of truth — referenced by docs/runbook tests)
# ─────────────────────────────────────────────────────────────────────────────

ENSEMBLE_ATTESTATION_MODE_ENV = "ENSEMBLE_LEADER_ATTESTATION_MODE"
ENSEMBLE_ATTESTATION_WINDOW_ENV = "ENSEMBLE_LEADER_ATTESTATION_WINDOW"
ENSEMBLE_ATTESTATION_DENY_BOUND_ENV = "ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND"

# ─────────────────────────────────────────────────────────────────────────────
# Defaults — referenced VERBATIM by the runbook drift test
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_MODE: Literal["dry"] = "dry"
DEFAULT_WINDOW: int = 3
DEFAULT_DENY_BOUND: int = 3

_VALID_MODES: tuple[str, ...] = ("off", "dry", "enforce")


# ─────────────────────────────────────────────────────────────────────────────
# Promotion metrics — CANONICAL names (task 4.6)
# ─────────────────────────────────────────────────────────────────────────────

#: Counter: every dry-mode evaluation that ran (decision=dry_log).
METRIC_DRY_LOG_TOTAL = "dry_log_total"
#: Subdivision of dry_log_total — evaluations whose R2-deny predicate
#: was satisfied (i.e. would have denied under mode="enforce"). Operators
#: query ``dry_log_deny_predicate_total / dry_log_total`` to adjudicate
#: the dry→enforce flip (false-positive rate).
METRIC_DRY_LOG_DENY_PREDICATE_TOTAL = "dry_log_deny_predicate_total"
#: Counter: every enforce-mode DENIED evaluation (decision=denied).
METRIC_ENFORCE_DENIED_TOTAL = "enforce_denied_total"


# ─────────────────────────────────────────────────────────────────────────────
# Config shape
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AttestationConfig:
    """The resolver's output — consumed by the gate + the boot-log emitter.

    Attributes:
        mode: Tri-state mode (``"off"`` / ``"dry"`` / ``"enforce"``).
            Default ``"dry"`` per D2 RESOLVED.
        window: Attestation lookback window N (default 3 per D4).
        deny_bound: Per-instance denied-completion bound (default 3 per D5).
        attestation_enabled: Convenience flag — ``mode != "off"``. The
            gate's INDEPENDENT master flag (C2) threads through this
            rather than reading the env directly.
        scope_applicable: Convenience flag — leader scope (D3, leader-only).
            Phase 4 ships this as ``True`` unconditionally; D3 narrowing
            is Phase 6.
    """

    mode: Literal["off", "dry", "enforce"]
    window: int
    deny_bound: int
    attestation_enabled: bool
    scope_applicable: bool


# ─────────────────────────────────────────────────────────────────────────────
# Pattern C cached-global state — restart-read; flip requires restart
# ─────────────────────────────────────────────────────────────────────────────

_CACHED_CONFIG: AttestationConfig | None = None
_BOOT_LOG_EMITTED: bool = False
#: Module-level one-shot WARN emitter — a WARN is emitted on the first
#: resolution that encounters an invalid value; subsequent resolutions
#: are silent (the cached global makes invalid-env WARNs idempotent).
_INVALID_VALUE_WARN_EMITTED: bool = False
#: Per-metric monotonic counters — emitted via INFO log on first request
#: (idempotent on subsequent requests) so operators can grep for the
#: canonical names. The Phase 5/6 enforcement will swap to a real
#: metrics sink; the canonical names are the migration contract.
_METRIC_COUNTERS: dict[str, int] = {
    METRIC_DRY_LOG_TOTAL: 0,
    METRIC_DRY_LOG_DENY_PREDICATE_TOTAL: 0,
    METRIC_ENFORCE_DENIED_TOTAL: 0,
}
_METRIC_LOG_EMITTED: dict[str, bool] = {
    METRIC_DRY_LOG_TOTAL: False,
    METRIC_DRY_LOG_DENY_PREDICATE_TOTAL: False,
    METRIC_ENFORCE_DENIED_TOTAL: False,
}


def reset_attestation_resolver_for_tests() -> None:
    """Clear the cached resolver + boot-log + metric counters.

    Test-only — production code never invokes this. Mirrors the WC-wake
    resolver's ``_reset_wc_wake_enqueue_for_tests`` helper. The
    ``AttestationConfig`` dataclass is frozen, so callers should mutate
    env vars THEN call this helper THEN call :func:`get_config` to
    re-resolve under the new env.
    """
    global _CACHED_CONFIG, _BOOT_LOG_EMITTED, _INVALID_VALUE_WARN_EMITTED
    _CACHED_CONFIG = None
    _BOOT_LOG_EMITTED = False
    _INVALID_VALUE_WARN_EMITTED = False
    for key in _METRIC_COUNTERS:
        _METRIC_COUNTERS[key] = 0
        _METRIC_LOG_EMITTED[key] = False


# ─────────────────────────────────────────────────────────────────────────────
# Pure env-parsing helpers (no logging; no I/O — unit-testable in isolation)
# ─────────────────────────────────────────────────────────────────────────────


def _parse_mode(source: dict[str, str]) -> str:
    """Parse the tri-state mode env, failing OPEN to ``"dry"``.

    Per ruling 4: invalid values resolve to ``"dry"`` (the default) and
    emit a one-shot WARN via the caller. Blank / unset also resolves to
    ``"dry"`` (mirroring the WC-wake resolver shape — blank is NOT
    treated as ``"off"``; the plan task 4.1 prose originally offered
    "blank → off" but the actual resolved value is ``"dry"`` per D2
    RESOLVED default and the requirements.md mode-glossary entry that
    specifies ``default dry at ship``).
    """
    raw = source.get(ENSEMBLE_ATTESTATION_MODE_ENV, "")
    if raw is None:
        raw = ""
    raw = str(raw).strip().lower()
    if not raw:
        return DEFAULT_MODE
    if raw in _VALID_MODES:
        return raw
    return DEFAULT_MODE  # fail-OPEN; caller logs WARN


def _parse_positive_int(
    source: dict[str, str],
    key: str,
    default: int,
) -> int:
    """Parse a positive-int env, failing OPEN to ``default``.

    Per ruling 4: invalid values resolve to ``default`` and emit a
    one-shot WARN via the caller. Values ``< 1`` also fail OPEN.
    """
    raw = source.get(key, "")
    if raw is None:
        raw = ""
    raw = str(raw).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < 1:
        return default
    return value


def _resolve_config_from_env(source: dict[str, str]) -> AttestationConfig:
    """Pure env → config resolver. No logging. No I/O.

    Returns an :class:`AttestationConfig` with ``mode`` defaulted to
    ``"dry"`` on any failure (ruling 4 — fail-OPEN).
    """
    mode = _parse_mode(source)
    window = _parse_positive_int(
        source, ENSEMBLE_ATTESTATION_WINDOW_ENV, DEFAULT_WINDOW
    )
    deny_bound = _parse_positive_int(
        source, ENSEMBLE_ATTESTATION_DENY_BOUND_ENV, DEFAULT_DENY_BOUND
    )
    return AttestationConfig(
        mode=mode,  # type: ignore[arg-type]
        window=window,
        deny_bound=deny_bound,
        attestation_enabled=(mode != "off"),
        scope_applicable=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Cached global resolver — Pattern C (restart-read; cached on first access)
# ─────────────────────────────────────────────────────────────────────────────


def get_config() -> AttestationConfig:
    """Return the cached :class:`AttestationConfig` (Pattern C, restart-read).

    The first call resolves the three env vars, emits a one-shot WARN
    for any invalid value (fail-OPEN), and caches the result. Subsequent
    calls return the cached value without re-reading the env — flip
    requires a daemon restart (per C-2 closed-by-user constraint).

    The cached ``AttestationConfig`` is the SINGLE source of truth for
    ``mode`` / ``window`` / ``deny_bound`` — the gate imports this
    function and reads through it; the gate MUST NOT re-resolve the env
    directly (the dual-source-of-truth seam would re-introduce the
    drift bug class this module exists to eliminate).
    """
    global _CACHED_CONFIG
    if _CACHED_CONFIG is not None:
        return _CACHED_CONFIG

    source = {k: os.environ.get(k, "") for k in (
        ENSEMBLE_ATTESTATION_MODE_ENV,
        ENSEMBLE_ATTESTATION_WINDOW_ENV,
        ENSEMBLE_ATTESTATION_DENY_BOUND_ENV,
    )}
    config = _resolve_config_from_env(source)

    # Emit any invalid-value WARNs (one-shot across the process).
    for warning in _collect_invalid_warnings(source):
        _emit_one_shot_warn(warning)

    _CACHED_CONFIG = config
    return config


def _emit_one_shot_warn(detail: str) -> None:
    """Emit a one-shot WARN naming any invalid env value encountered.

    Mirrors the WC-wake resolver's "_resolve_wc_wake_enqueue_enabled"
    one-shot WARN on unknown values — the cache makes the WARN idempotent
    across calls within the same process lifetime.
    """
    global _INVALID_VALUE_WARN_EMITTED
    if _INVALID_VALUE_WARN_EMITTED:
        return
    _INVALID_VALUE_WARN_EMITTED = True
    logger.warning(detail)


def _collect_invalid_warnings(source: dict[str, str]) -> list[str]:
    """Return a list of ``"ENV=raw is not …"`` warnings to emit one-shot.

    Empty list means every env value parsed cleanly. The function is
    pure (no I/O) so it's unit-testable.
    """
    warnings: list[str] = []
    mode_raw = str(source.get(ENSEMBLE_ATTESTATION_MODE_ENV, "") or "").strip().lower()
    if mode_raw and mode_raw not in _VALID_MODES:
        warnings.append(
            "%s=%r is not a recognized mode (off|dry|enforce); "
            "failing OPEN to the default 'dry'. Restart required after "
            "fixing the env value."
            % (ENSEMBLE_ATTESTATION_MODE_ENV, mode_raw)
        )
    for key, default in (
        (ENSEMBLE_ATTESTATION_WINDOW_ENV, DEFAULT_WINDOW),
        (ENSEMBLE_ATTESTATION_DENY_BOUND_ENV, DEFAULT_DENY_BOUND),
    ):
        raw = str(source.get(key, "") or "").strip()
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            warnings.append(
                "%s=%r is not a valid integer; failing OPEN to the "
                "default %d." % (key, raw, default)
            )
            continue
        if value < 1:
            warnings.append(
                "%s=%r is < 1; failing OPEN to the default %d."
                % (key, raw, default)
            )
    return warnings


# ─────────────────────────────────────────────────────────────────────────────
# Boot log + O1 boot assert
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_min_recent_window() -> int:
    """Resolve the compaction floor at boot time.

    Reads ``daemon.constants.MIN_RECENT_WINDOW`` lazily (import-time
    failure would defeat the resolver's fail-OPEN contract — the
    daemon should boot even if the constants module is broken). Returns
    the documented default ``3`` on import failure so the O1 assert
    degrades to WARN-once when ``WINDOW > 3``.
    """
    try:
        from .constants import MIN_RECENT_WINDOW

        return int(MIN_RECENT_WINDOW)
    except Exception:  # noqa: BLE001 — O1 floor must not break boot
        return 3


def _format_window_floor_status(window: int, floor: int) -> tuple[str, str]:
    """Return ``(PASS|WARN, message)`` for the O1 boot assert.

    ``WINDOW <= floor`` ⇒ PASS (no advisory). ``WINDOW > floor`` ⇒ WARN
    naming the operator-visible risk. The shape mirrors the WC-wake
    boot log (``emit_wc_wake_enqueue_boot_log`` — single INFO line with
    inline PASS/WARN tagging).
    """
    if window <= floor:
        return "PASS", ""
    detail = (
        "N_le_min_recent_window=WARN: WINDOW=%d > min_recent_window=%d; "
        "attestation_denied_count risk: aggressive context pressure may "
        "fold the attestation tool_call (gate continues running — "
        "WARN-only, never fail-closed)."
        % (window, floor)
    )
    return "WARN", detail


def emit_attestation_boot_log() -> None:
    """Emit the one-time boot-time INFO log announcing resolved values.

    Called by :class:`InstanceManager.__init__` after the messaging
    service is wired (mirrors :func:`emit_wc_wake_enqueue_boot_log` /
    :func:`emit_governor_recursion_guard_boot_log`).

    Format (single INFO line):

    ::

        Leader completion attestation resolved: mode=<off|dry|enforce>
            window=<N> deny_bound=<N> attestation_enabled=<true|false>
            N_le_min_recent_window=<PASS|WARN> (env MODE=, WINDOW=, BOUND=)
            Restart required to flip.

    The line is emitted EXACTLY ONCE per process. The O1 WARN is
    appended as a SEPARATE WARN-level line — operator log-aggregators
    filter WARN separately from INFO (the runbook documents this
    dual-line shape).
    """
    global _BOOT_LOG_EMITTED
    if _BOOT_LOG_EMITTED:
        return
    _BOOT_LOG_EMITTED = True

    config = get_config()
    floor = _resolve_min_recent_window()
    floor_status, floor_detail = _format_window_floor_status(
        config.window, floor
    )

    logger.info(
        "Leader completion attestation resolved: mode=%s window=%d "
        "deny_bound=%d attestation_enabled=%s "
        "N_le_min_recent_window=%s "
        "(env %s=%s, %s=%s, %s=%s). "
        "Restart required to flip. See docs/setup.md "
        "(ENSEMBLE_LEADER_ATTESTATION_MODE).",
        config.mode,
        config.window,
        config.deny_bound,
        "true" if config.attestation_enabled else "false",
        floor_status,
        ENSEMBLE_ATTESTATION_MODE_ENV,
        os.environ.get(ENSEMBLE_ATTESTATION_MODE_ENV, "<unset>"),
        ENSEMBLE_ATTESTATION_WINDOW_ENV,
        os.environ.get(ENSEMBLE_ATTESTATION_WINDOW_ENV, "<unset>"),
        ENSEMBLE_ATTESTATION_DENY_BOUND_ENV,
        os.environ.get(ENSEMBLE_ATTESTATION_DENY_BOUND_ENV, "<unset>"),
    )

    if floor_status == "WARN":
        logger.warning(floor_detail)


# ─────────────────────────────────────────────────────────────────────────────
# Promotion metrics — task 4.6
# ─────────────────────────────────────────────────────────────────────────────


#: TypedDict surface for callers that want to read the counters (Phase 6
#: will swap to a real metrics sink; the dict is the contract).
class PromotionMetricsSnapshot(TypedDict):
    dry_log_total: int
    dry_log_deny_predicate_total: int
    enforce_denied_total: int


def record_promotion_metric(
    name: Literal[
        "dry_log_total",
        "dry_log_deny_predicate_total",
        "enforce_denied_total",
    ],
    *,
    increment: int = 1,
) -> int:
    """Increment one of the three CANONICAL promotion-metric counters.

    Returns the post-increment counter value. The first call for each
    canonical name ALSO emits an INFO log announcing the metric name
    so operators can grep for it (``promotion_metric: name=…``). The
    counter values are kept in module state; the resolver owns them
    so the gate / wiring can record without a circular dependency on
    the metrics sink (Phase 6 will swap the storage to the metrics
    service — the canonical names are the migration contract).
    """
    if name not in _METRIC_COUNTERS:
        raise ValueError(
            "unknown promotion metric name=%r; valid names are: %s"
            % (name, sorted(_METRIC_COUNTERS))
        )
    _METRIC_COUNTERS[name] += increment
    if not _METRIC_LOG_EMITTED[name]:
        _METRIC_LOG_EMITTED[name] = True
        logger.info(
            "promotion_metric: name=%s registered (canonical Phase 4 task "
            "4.6 metric — operators query this for the dry→enforce "
            "flip adjudication).",
            name,
        )
    return _METRIC_COUNTERS[name]


def get_promotion_metrics() -> PromotionMetricsSnapshot:
    """Snapshot of the current counter values (for runbook drift tests +
    future Phase-6 metrics-export wiring). Returns a copy — mutating
    the dict does not affect internal state.
    """
    return PromotionMetricsSnapshot(
        dry_log_total=_METRIC_COUNTERS[METRIC_DRY_LOG_TOTAL],
        dry_log_deny_predicate_total=_METRIC_COUNTERS[
            METRIC_DRY_LOG_DENY_PREDICATE_TOTAL
        ],
        enforce_denied_total=_METRIC_COUNTERS[METRIC_ENFORCE_DENIED_TOTAL],
    )