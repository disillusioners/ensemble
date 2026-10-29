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
value is read from the managed-key ladder's DB tier (the
``app_settings`` override rows) by a **REFRESHER THREAD** — never by a
request thread, never by the event loop, never under a metric's lock.
The census reads the cached value only — a PURE read, no I/O — so the
census stays pool-free.

THE LIVE MANAGED-VALUE RESOLVER (MAJOR-2 fix)
---------------------------------------------

The resolver serves **every** live managed key from ONE DB read: the
provider returns the managed-key ladder's DB tier (the ``app_settings``
override rows) as a MAPPING, and the resolver caches it for
``MANAGED_REFRESH_TTL_S``. Three keys are live against that cache:

* ``limits.metrics_enabled`` — the kill-switch (case (iii));
* ``limits.metrics_scrape_interval_hint_s`` — the registry's publisher
  cadence (plan §5: "No (per-request)");
* ``limits.metrics_cardinality_cap`` — the registry's HARD cap (plan §5:
  "No (per-request)"; §13's back-out relies on "raise the cap … no
  restart").

The last TWO used to be served from the BOOT-APPLIED config object
while the settings API advertised "no restart needed" — the asymmetry
the review flagged. They now read the SAME live cache, so a runtime
``PUT /api/settings/daemon/{key}`` is reflected by the registry
providers without a restart, within the TTL.

FOUR INVARIANTS THE RESOLVER KEEPS (FIX ROUND 1 / F1):

* **The request path NEVER touches the DB.** ``request_managed_values_refresh``
  only SIGNALS the refresher thread and returns the last-known values.
  A pooled checkout can block for tens of seconds under the Oct-26/28
  pool-poisoning class — Prometheus's ``scrape_timeout`` is 10 s, so a
  scrape that waits for a checkout turns into ``up==0`` exactly when
  the data matters most.
* **The env tier wins — no DB touch at all.** When
  ``ENSEMBLE_METRICS_ENABLED`` is EXPLICITLY set, the managed chain is
  irrelevant: the resolver performs NO read (neither on the request
  path nor on the refresher). The live cadence/cap then keep serving
  their LAST-KNOWN values until the env is unset again.
* **Single-flight.** A refresh in progress is never stacked: later
  callers get the last-known values immediately.
* **A key with no DB override falls back to its BOOT value** (the DB
  tier is absent ⇒ the yaml/env/default tier still applies), and the
  cached read is **PURE** — the registry consults its providers while
  holding a metric's own lock, so a DB round trip there would be the
  CR-1 wedged-writer failure.

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
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

#: The kill-switch env var — the authoritative per-request off-switch.
ENV_VAR = "ENSEMBLE_METRICS_ENABLED"

#: The ONLY values that mean ON (case-insensitive). Everything else
#: that is EXPLICITLY set means OFF (fail closed).
TRUTHY_VALUES: frozenset[str] = frozenset({"1", "true", "yes"})

#: The managed key that mirrors the env switch (DB-backed tier).
MANAGED_KEY = "limits.metrics_enabled"

#: The managed key that carries the registry's publisher cadence (live).
SCRAPE_INTERVAL_KEY = "limits.metrics_scrape_interval_hint_s"

#: The managed key that carries the registry's HARD cardinality cap
#: (live).
CARDINALITY_CAP_KEY = "limits.metrics_cardinality_cap"

#: The ONE boot log line (plan §5 / catalog §0.8). Emitted exactly once
#: per process when the env is UNSET while the managed key is ON — the
#: state must never be mysterious.
DEFER_LOG_MESSAGE = (
    "`ENSEMBLE_METRICS_ENABLED` unset; deferring to `limits.metrics_enabled`"
)

#: How long a cached managed-key value stays fresh. The managed tier is
#: DB-backed; the refresh rides the REFRESHER thread (never a request
#: thread), so the census sampler never touches a pool slot for a
#: boolean.
MANAGED_REFRESH_TTL_S = 5.0

_lock = threading.Lock()
_managed_enabled: bool = False
_managed_values: dict[str, Any] = {}
_managed_provider: Callable[[], Mapping[str, Any] | None] | None = None
_managed_refreshed_at: float = 0.0
_defer_log_emitted: bool = False

#: The refresher thread — the ONLY thread that reads the managed-key DB
#: tier. ONE per process; started lazily on the first refresh request.
_refresher_thread: threading.Thread | None = None
_refresher_wake = threading.Event()
_refresher_stop = threading.Event()
_refresher_lock = threading.Lock()
#: The refresher generation — a restarted thread retires the previous
#: one (two generations never read at the same time).
_refresher_generation = 0
#: The single-flight flag: a refresh in progress is never stacked.
_refreshing = False
#: When a refresh was last REQUESTED (the request path's TTL gate).
_refresh_requested_at: float = 0.0


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


# ─── managed-key tier (DB-backed; refreshed by the refresher thread) ──────


def set_managed_metrics_enabled(value: bool) -> None:
    """Seed the managed-key tier (boot / test seam).

    The seeded value is the BOOT fallback: it is used until (and after)
    the live cache holds a DB override for the key.
    """
    global _managed_enabled, _managed_refreshed_at
    with _lock:
        _managed_enabled = bool(value)
        _managed_refreshed_at = time.monotonic()


def build_managed_values_provider(
    read_overrides: Callable[[], Mapping[str, Any]],
) -> Callable[[], Mapping[str, Any]]:
    """Wrap the managed-key DB-tier reader as the resolver's provider.

    ``read_overrides`` returns the managed-key ladder's DB tier — the
    ``app_settings`` override rows the settings API writes
    (``{key: value}``). The provider normalises it to a plain dict; a
    reader that RAISES is handled by the resolver (the last known
    values are kept — never a crash, never a silent reset).
    """

    def _provider() -> Mapping[str, Any]:
        values = read_overrides()
        return dict(values or {})

    return _provider


def set_managed_metrics_provider(
    provider: Callable[[], Mapping[str, Any] | None] | None,
) -> None:
    """Install the live managed-values provider (boot wiring / test seam).

    The provider returns the CURRENT DB tier of the managed-key ladder
    (``{key: value}``). It may hit the DB; the REFRESHER thread owns
    that cost (the request path only signals it).
    """
    global _managed_provider
    with _lock:
        _managed_provider = provider


def managed_metrics_enabled() -> bool:
    """The cached gate value (PURE read — no I/O)."""
    with _lock:
        return _managed_enabled


def live_managed_value(key: str, default: Any) -> Any:
    """The cached LIVE value of a managed key (PURE read — no I/O).

    This is what the registry's providers call: they are consulted
    while a metric's own lock is held, so a DB round trip here would
    be the CR-1 wedged-writer failure. The cache is refreshed by the
    REFRESHER THREAD (:func:`refresh_managed_values`).

    A key with no DB override falls back to ``default`` — the boot
    value (the yaml/env/default tier still applies).
    """
    with _lock:
        values = _managed_values
    if key in values:
        return values[key]
    return default


def managed_values_snapshot() -> Mapping[str, Any]:
    """The cached live managed values (PURE read — no I/O)."""
    with _lock:
        return dict(_managed_values)


def request_managed_values_refresh(
    *,
    ttl_s: float = MANAGED_REFRESH_TTL_S,
) -> Mapping[str, Any]:
    """The REQUEST/SERVE-PATH entry point — NEVER blocks, NEVER stacks.

    * **The env tier wins — NO DB touch at all.** When
      ``ENSEMBLE_METRICS_ENABLED`` is EXPLICITLY set the managed chain
      is irrelevant, so the resolver performs no read at all (neither
      here nor on the refresher). The emergency-off path pays nothing.
    * **Single-flight.** A refresh already in flight is never stacked;
      the caller gets the last-known values.
    * **The DB read never happens on a request thread.** This function
      only SIGNALS the refresher thread and returns immediately with
      the last-known values: a pooled checkout can block for tens of
      seconds under the pool-poisoning class, and a scrape must never
      wait for it (Prometheus's ``scrape_timeout`` is 10 s — a blocked
      scrape is an ``up==0``).

    The request path is TTL-gated: at most one refresh is REQUESTED
    per ``ttl_s`` seconds, so a scrape burst cannot turn into a burst
    of DB reads.
    """
    global _refresh_requested_at
    if env_explicit_enabled() is not None:
        # The env tier is the authority — the managed chain is not read.
        return managed_values_snapshot()
    now = time.monotonic()
    with _lock:
        if _refreshing:
            # A refresh is in flight — the last-known values stand.
            skip = True
        elif now - _refresh_requested_at < ttl_s:
            # The TTL window: no new refresh is requested.
            skip = True
        else:
            skip = False
            _refresh_requested_at = now
    if skip:
        return managed_values_snapshot()
    _ensure_refresher()
    _refresher_wake.set()
    return managed_values_snapshot()


def _ensure_refresher() -> None:
    """Start the refresher thread (lazily, once per process).

    A generation counter retires the previous thread when it is
    restarted (a test reset stops it) — so two refresher generations
    can never read at the same time.
    """
    global _refresher_thread, _refresher_generation
    with _refresher_lock:
        if (
            _refresher_thread is not None
            and _refresher_thread.is_alive()
            and not _refresher_stop.is_set()
        ):
            return
        _refresher_generation += 1
        generation = _refresher_generation
        _refresher_stop.clear()
        _refresher_thread = threading.Thread(
            target=_refresher_loop,
            args=(generation,),
            name="metrics-managed-refresh",
            daemon=True,
        )
        _refresher_thread.start()


def _refresher_loop(generation: int) -> None:
    """The refresher loop — the ONLY thread that reads the DB tier.

    It wakes on the request path's signal (or on the TTL timeout) and
    performs ONE read. A read that blocks (a poisoned pool) stalls
    THIS thread only: the last-known values keep being served.
    """
    while True:
        _refresher_wake.wait(timeout=MANAGED_REFRESH_TTL_S)
        _refresher_wake.clear()
        if _refresher_stop.is_set() or generation != _refresher_generation:
            return
        try:
            if env_explicit_enabled() is not None:
                # The env tier wins — no DB touch at all.
                continue
            refresh_managed_values()
        except Exception as exc:  # noqa: BLE001 — the refresher never dies
            logger.warning(
                "[Metrics] managed-key refresher failed (%s: %s)",
                type(exc).__name__,
                exc,
            )


def build_registry_providers(
    *,
    scrape_interval_default: int,
    cardinality_cap_default: int,
) -> tuple[Callable[[], float], Callable[[], int]]:
    """Build the registry's two LIVE managed-value providers.

    The publisher cadence (``limits.metrics_scrape_interval_hint_s``)
    and the HARD cardinality cap (``limits.metrics_cardinality_cap``)
    are live managed keys (plan §5: "No (per-request)"; §13's back-out
    relies on "raise the cap … no restart"). They read the LIVE
    managed-value cache — refreshed from the DB tier by the REFRESHER
    thread — and fall back to the BOOT value when the managed chain
    carries no DB override for the key.

    The reads are PURE (a cached lookup): the registry consults its
    providers while holding a metric's own lock, so a DB round trip
    there would be the CR-1 wedged-writer failure.

    Returns:
        ``(publisher_cadence_provider, cardinality_cap_provider)``.
    """

    def _publisher_cadence() -> float:
        return float(
            live_managed_value(SCRAPE_INTERVAL_KEY, scrape_interval_default)
        )

    def _cardinality_cap() -> int:
        return int(
            live_managed_value(CARDINALITY_CAP_KEY, cardinality_cap_default)
        )

    return _publisher_cadence, _cardinality_cap


def refresh_managed_values(
    *,
    now: float | None = None,
    ttl_s: float = MANAGED_REFRESH_TTL_S,
) -> Mapping[str, Any]:
    """Refresh the cached live managed values from the provider.

    ONE DB read serves EVERY live managed key (the kill-switch, the
    publisher cadence, the cardinality cap). The read is
    TTL-bounded (at most one read per ``ttl_s`` seconds) and
    SINGLE-FLIGHT: a read already in flight is never stacked — the
    later caller gets the last-known values.

    This is the REFRESHER THREAD's entry point (and the tests' seam).
    It MUST NOT be called from a request path: a pooled checkout can
    block for tens of seconds.

    A provider failure is FAIL-SAFE: the last known values are kept
    (never a spurious OFF that would silently darken the surface, never
    a spurious ON that would override an explicit env OFF — the env tier
    always wins anyway).
    """
    global _managed_values, _managed_enabled, _managed_refreshed_at
    global _refreshing
    with _lock:
        provider = _managed_provider
        current_values = dict(_managed_values)
        refreshed_at = _managed_refreshed_at
        if _refreshing:
            # Single-flight: a read is in flight — never stack it.
            return current_values
        if provider is None:
            return current_values
        stamp = time.monotonic() if now is None else now
        if stamp - refreshed_at < ttl_s:
            return current_values
        _refreshing = True
    try:
        values = dict(provider() or {})
        with _lock:
            _managed_values = values
            if MANAGED_KEY in values:
                # The kill-switch's DB tier — when the DB has no override
                # row the BOOT fallback stays in force.
                _managed_enabled = bool(values[MANAGED_KEY])
            _managed_refreshed_at = stamp
        return values
    except Exception as exc:  # noqa: BLE001 — never raise into the caller
        logger.warning(
            "[Metrics] managed-key refresh failed (%s: %s) — keeping the "
            "last known values",
            type(exc).__name__,
            exc,
        )
        return current_values
    finally:
        # ONE reset path (H1): the single-flight flag is cleared in
        # exactly one place, so no failure between two reset sites can
        # leave the flag LATCHED (a latched flag would stall every
        # later refresh).
        with _lock:
            _refreshing = False


def refresh_managed_metrics_enabled(
    *,
    now: float | None = None,
    ttl_s: float = MANAGED_REFRESH_TTL_S,
) -> bool:
    """Refresh the live managed values and return the gate's value.

    A thin wrapper over :func:`refresh_managed_values` — ONE DB read
    serves every live key. It performs the READ, so it belongs to the
    REFRESHER THREAD (never to a request path — see
    :func:`request_managed_values_refresh`).
    """
    refresh_managed_values(now=now, ttl_s=ttl_s)
    return managed_metrics_enabled()


# ─── the gate ───────────────────────────────────────────────────────────────


def metrics_enabled() -> bool:
    """THE GATE — the single kill-switch decision (one rule, three cases).

    Import-cheap and I/O-free: the env tier is read per call; the
    managed tier is the cached value refreshed by the REFRESHER THREAD.
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
    global _managed_enabled, _managed_values, _managed_provider
    global _managed_refreshed_at, _defer_log_emitted
    global _refreshing, _refresh_requested_at
    _refresher_stop.set()
    _refresher_wake.set()
    with _lock:
        _managed_enabled = False
        _managed_values = {}
        _managed_provider = None
        _managed_refreshed_at = 0.0
        _defer_log_emitted = False
        _refreshing = False
        _refresh_requested_at = 0.0
