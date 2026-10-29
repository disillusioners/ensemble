"""Metrics kill-switch gate — ``ENSEMBLE_METRICS_ENABLED`` (P0 T3).

THE ONE RULE, THREE CASES (plan-overview §5 / metric-catalog §0.8,
amendment A14 / NB-6)
------------------------------------------------------------------------

    (i)   env EXPLICITLY SET (present in the environment — INCLUDING
          set-but-empty) → the truthy set is ``{1, true, yes}``
          (case-insensitive) ⇒ ON. EVERY other explicit value is OFF
          (``ture``, ``on``, ``enabled``, ``0``, ``""``) — an
          UNRECOGNISED EXPLICIT VALUE FAILS CLOSED.
    (ii)  explicit OFF ⇒ OFF, and it WINS over the managed-key chain
          (the emergency off-switch — no restart, no ambiguity).
    (iii) env UNSET (absent) ⇒ the MANAGED-KEY CHAIN decides
          (``limits.metrics_enabled`` on ⇒ ON — env-unset is NOT a
          statement of intent; managed-key default ⇒ OFF).

"Fail closed" therefore describes ONLY an invalid EXPLICIT value — it
NEVER describes an unset variable. This distinction is the whole point
of the rule: an operator who never touched the env must not be able to
accidentally expose the exposition surface, and an operator who sets a
garbage value must get the safe side of the switch.

READ COST (arch §3 Focus-3 (a))
------------------------------

The env read is a plain ``os.environ.get`` — a dict-backed mapping
lookup (~50–100 ns, NO syscall). It happens PER REQUEST, with NO TTL
cache: a cached env read would add staleness that defeats the entire
emergency-off purpose (the arch round explicitly REJECTED the TTL
cache). At a 15 s scrape cadence this is ~7 ns/s.

THE MANAGED-KEY TIER
--------------------

``limits.metrics_enabled`` (managed key, ``daemon/services/
settings_service.py``) is the DB-backed tier of the same switch. Its
value is resolved ONCE per ``MANAGED_REFRESH_TTL_S`` by the LISTENER
thread (the only thread allowed to touch the DB for this — the census
sampler is deliberately non-pooled and MUST NOT consume a pool slot
for a boolean). The census reads the cached value only — a PURE read,
no I/O — so the census stays pool-free.

This module is import-safe and import-light on purpose: BOTH the
listener (``daemon/services/metrics_listener.py``) and the census
sampler (``daemon/services/metrics_census.py``) import it, and NEITHER
may import ``daemon/api.py`` (that would create an import cycle and
drag the whole FastAPI app into a stdlib HTTP server module).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

#: The kill-switch env var — the authoritative per-request off-switch.
ENV_VAR = "ENSEMBLE_METRICS_ENABLED"

#: The ONLY values that mean ON (case-insensitive). Everything else
#: that is EXPLICITLY set means OFF (fail closed).
TRUTHY_VALUES: frozenset[str] = frozenset({"1", "true", "yes"})

#: The managed key that mirrors the env switch (DB-backed tier).
MANAGED_KEY = "limits.metrics_enabled"

#: The ONE boot log line (plan §5 / catalog §0.8). Emitted exactly once
#: per process when the env is UNSET while the managed key is ON — the
#: state must never be mysterious.
DEFER_LOG_MESSAGE = (
    "`ENSEMBLE_METRICS_ENABLED` unset; deferring to `limits.metrics_enabled`"
)

#: How long a cached managed-key value stays fresh. The managed tier is
#: DB-backed; the refresh rides the listener's dedicated thread so the
#: census sampler never touches a pool slot for a boolean.
MANAGED_REFRESH_TTL_S = 5.0

_lock = threading.Lock()
_managed_enabled: bool = False
_managed_provider: Callable[[], bool] | None = None
_managed_refreshed_at: float = 0.0
_defer_log_emitted: bool = False


# ─── env tier (per request, no cache) ───────────────────────────────────────


def env_raw() -> str | None:
    """The raw env value, or ``None`` when the var is UNSET.

    ``""`` (set-but-empty) is NOT ``None`` — the two states differ
    (case (i) vs case (iii)).
    """
    return os.environ.get(ENV_VAR)


def env_explicit_enabled() -> bool | None:
    """The env tier's decision.

    Returns:
        ``True`` — explicitly set to a truthy value.
        ``False`` — explicitly set to ANY other value (fail closed).
        ``None`` — the env var is UNSET; the managed-key chain decides.
    """
    raw = os.environ.get(ENV_VAR)
    if raw is None:
        return None
    # Case-insensitive membership ONLY — no stripping, no aliases. A
    # value with surrounding whitespace is an UNRECOGNISED explicit
    # value and therefore fails closed.
    return raw.lower() in TRUTHY_VALUES


# ─── managed-key tier (DB-backed; refreshed by the listener thread) ────────


def set_managed_metrics_enabled(value: bool) -> None:
    """Seed the managed-key tier (boot / test seam)."""
    global _managed_enabled, _managed_refreshed_at
    with _lock:
        _managed_enabled = bool(value)
        _managed_refreshed_at = time.monotonic()


def set_managed_metrics_provider(
    provider: Callable[[], bool] | None,
) -> None:
    """Install the managed-key value provider (boot wiring / test seam).

    The provider returns the CURRENT effective managed value. It may hit
    the DB; the caller (the listener thread) owns that cost.
    """
    global _managed_provider
    with _lock:
        _managed_provider = provider


def managed_metrics_enabled() -> bool:
    """The cached managed-key value (PURE read — no I/O)."""
    with _lock:
        return _managed_enabled


def refresh_managed_metrics_enabled(
    *,
    now: float | None = None,
    ttl_s: float = MANAGED_REFRESH_TTL_S,
) -> bool:
    """Refresh the cached managed-key value from the installed provider.

    Returns the effective managed-key value. A provider failure is
    FAIL-SAFE: the last known value is kept (never a spurious OFF that
    would silently darken the surface, never a spurious ON that would
    override an explicit env OFF — the env tier always wins anyway).
    """
    global _managed_enabled, _managed_refreshed_at
    with _lock:
        provider = _managed_provider
        current = _managed_enabled
        refreshed_at = _managed_refreshed_at
    if provider is None:
        return current
    stamp = time.monotonic() if now is None else now
    if stamp - refreshed_at < ttl_s:
        return current
    try:
        value = bool(provider())
    except Exception as exc:  # noqa: BLE001 — never raise into the caller
        logger.warning(
            "[Metrics] managed-key refresh failed (%s: %s) — keeping the "
            "last known value",
            type(exc).__name__,
            exc,
        )
        return current
    with _lock:
        _managed_enabled = value
        _managed_refreshed_at = stamp
    return value


# ─── the gate ───────────────────────────────────────────────────────────────


def metrics_enabled() -> bool:
    """THE GATE — the single kill-switch decision (one rule, three cases).

    Import-cheap and I/O-free: the env tier is read per call; the
    managed tier is the cached value refreshed by the listener thread.
    """
    explicit = env_explicit_enabled()
    if explicit is not None:
        return explicit
    return managed_metrics_enabled()


def log_deferral_once(*, force: bool = False) -> bool:
    """Emit the ONE boot log line (env unset + managed key ON).

    Returns ``True`` when the line was emitted by THIS call. Idempotent
    per process (the line fires exactly once).
    """
    global _defer_log_emitted
    if env_explicit_enabled() is not None:
        return False
    if not managed_metrics_enabled():
        return False
    with _lock:
        if _defer_log_emitted and not force:
            return False
        _defer_log_emitted = True
    logger.info("[Metrics] %s", DEFER_LOG_MESSAGE)
    return True


def reset_for_tests() -> None:
    """Reset the module state (test-only — mirrors the registry's
    ``reset_for_tests`` convention)."""
    global _managed_enabled, _managed_provider, _managed_refreshed_at
    global _defer_log_emitted
    with _lock:
        _managed_enabled = False
        _managed_provider = None
        _managed_refreshed_at = 0.0
        _defer_log_emitted = False
