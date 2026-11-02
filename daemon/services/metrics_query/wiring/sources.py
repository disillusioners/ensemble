"""WHERE the pull adapters read from — the producer handles.

The P4 families are **readers**: each adapter reads an existing
producer's already-computed value and writes it into the registry
(AD3).  This module is the ONE place that says WHERE those producers
live.

THE BRIDGE
----------
The producers live on the FastAPI application's ``state`` (the daemon's
own container): ``app.state.manager`` (the instance manager, the engine,
the worker pools), ``app.state.readiness_composite`` (the PUBLISHED
readiness composite), ``app.state.resource_monitor`` (the resource
spine), ``app.state.wedge_capture_watchdog`` (the lag probe + the
capture counters), ``app.state.metrics_census`` (the frozen
``CensusSnapshot``), ``app.state.mcps_gateway_service`` and the sweep
services.

This lane edits **NO mechanical-list file** (the plan's Tier-1
constraint: ``daemon/api.py`` is on the mechanical Tier-2 list, so a
one-line wiring edit there would force a whole-tree dual-side leg).  So
the bridge does NOT import ``daemon.api``: it looks the ALREADY-IMPORTED
module up in ``sys.modules``.  In the running daemon ``daemon.api`` is
always imported (the uvicorn entry point is ``daemon.api:app``), so
the lookup succeeds; in a unit test the module is usually absent and
the lookup returns ``None`` — every family then degrades to "no
series", which is the honest absent-≠-zero posture.

THE EXPLICIT OVERRIDE
---------------------
``bind_sources()`` lets a caller (a test, or a future lane with a
proper DI seam) install an explicit bundle.  The explicit bundle wins
over the locator, and ``unbind_sources()`` restores the locator.

NOTHING HERE READS THE DATABASE
-------------------------------
The DB-backed adapters get their DSN from ``resolve_dsn`` (the census's
own resolver) and open a ONE-SHOT connection per tick (AM-11) — see
``db.py``.  This module never opens a connection and never touches a
pool.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)


@dataclass
class WiringSources:
    """The producer handles the pull adapters READ.

    Every field is OPTIONAL: a missing producer degrades its family to
    "no series" (absent ≠ zero).  The dataclass is deliberately a plain
    bag of handles — it is a READ substrate, never a service.
    """

    #: The daemon's ``InstanceManager`` (the engine, the worker pools,
    #: the instance repository).
    manager: Any = None
    #: The resource-monitor facade (``sampler_counters`` /
    #: ``flusher_counters``).
    resource_monitor: Any = None
    #: The wedge-capture watchdog (the lag probe + the capture counters).
    wedge_watchdog: Any = None
    #: The ``CensusSampler`` (the frozen ``CensusSnapshot``).
    census: Any = None
    #: A callable returning the PUBLISHED readiness composite (or
    #: ``None``).  It is a CALLABLE because the composite is REPLACED
    #: on every readiness tick — an adapter must read the CURRENT one.
    readiness_composite: Callable[[], Any] | None = None
    #: A callable returning the MCP server-health snapshot (the
    #: ``/readyz`` ``detail.mcp`` map) or ``None``.
    mcp_snapshot: Callable[[], Mapping[str, Any] | None] | None = None
    #: The MCPS gateway service (the roster, the sessions, the
    #: tombstones).
    mcps_gateway_service: Any = None
    #: The sweep services by lane name (the ``counters()`` readers).
    sweeps: Mapping[str, Any] = field(default_factory=dict)
    #: The install dir the journal twin reads (``<dir>/releases/state.json``).
    #: It is a CALLABLE because the canonical resolver imports the whole
    #: tool registry — the import is deferred to the tick that needs it.
    install_dir: Callable[[], Any] | None = None
    #: The DSN the DB-backed adapters use (one-shot per tick).
    dsn: str | None = None
    #: The DB connect factory (injectable for tests; the production
    #: default is ``daemon.services.metrics_census.connect_census``'s
    #: sibling — see ``db.py``).
    db_connect: Callable[..., Any] | None = None


# ── the explicit override ────────────────────────────────────────────────
_BOUND_SOURCES: WiringSources | None = None


def bind_sources(sources: WiringSources | None) -> None:
    """Install (or clear) an EXPLICIT source bundle.

    The explicit bundle wins over the runtime locator.  ``None`` clears
    it and restores the locator.
    """
    global _BOUND_SOURCES
    _BOUND_SOURCES = sources
    global _RUNTIME_CACHE
    _RUNTIME_CACHE = None


def unbind_sources() -> None:
    """Restore the runtime locator (the explicit bundle is dropped)."""
    bind_sources(None)


def _locate_app_state() -> Any | None:
    """The live app's ``state`` — WITHOUT importing ``daemon.api``.

    ``sys.modules`` lookup only: importing ``daemon.api`` from here
    would (a) create a circular import (``daemon.api`` imports this
    package's parent) and (b) run ``create_app()`` at import time (the
    module creates TWO apps), which is far too heavy for a library
    import.  The running daemon always has ``daemon.api`` imported (the
    uvicorn entry is ``daemon.api:app``), so the lookup succeeds
    exactly where it matters.
    """
    module = sys.modules.get("daemon.api")
    if module is None:
        return None
    app = getattr(module, "app", None)
    if app is None:
        return None
    return getattr(app, "state", None)


def _resolve_install_dir() -> Any:
    """The daemon's OWN install-dir resolution (the canonical resolver).

    It honours the daemon's self-env, so a dev checkout resolves
    ``None`` and the journal family renders no series (it never reads a
    live-shaped path it was not given).
    """
    try:
        from daemon.tools.upgrade_tools import _resolve_install_dir as _resolve

        return _resolve(None)
    except Exception:  # noqa: BLE001 — the journal family degrades
        return None


def _build_runtime_sources(state: Any) -> WiringSources:
    """Build the bundle from the live app state."""
    if state is None:
        return WiringSources()

    manager = getattr(state, "manager", None)

    dsn: str | None = None
    engine = getattr(manager, "engine", None)
    if engine is not None:
        try:
            from daemon.services.metrics_census import resolve_dsn

            dsn = resolve_dsn(engine)
        except Exception:  # noqa: BLE001 — a missing DSN ⇒ no DB families
            dsn = None

    sweeps: dict[str, Any] = {}
    for lane, attribute in (
        ("watch_reconcile", "watch_reconcile_sweep"),
        ("eligible_pending", "eligible_pending_sweep"),
        ("orphan_watcher", "orphan_watcher_sweep"),
        ("job_lock", "job_lock_sweep"),
        ("upgrade_journal", "upgrade_journal_sweep"),
        ("stale_pause", "stale_pause_sweep"),
    ):
        service = getattr(state, attribute, None)
        if service is not None:
            sweeps[lane] = service

    return WiringSources(
        manager=manager,
        resource_monitor=getattr(state, "resource_monitor", None),
        wedge_watchdog=getattr(state, "wedge_capture_watchdog", None),
        census=getattr(state, "metrics_census", None),
        readiness_composite=lambda: getattr(state, "readiness_composite", None),
        mcp_snapshot=lambda: _read_mcp_snapshot(),
        mcps_gateway_service=getattr(state, "mcps_gateway_service", None),
        sweeps=sweeps,
        install_dir=_resolve_install_dir,
        dsn=dsn,
    )

def _read_mcp_snapshot() -> Mapping[str, Any] | None:
    """The per-server MCP health map (the ``/readyz`` ``detail.mcp``).

    A READ of the per-boot quarantine registry merged with the built-in
    registry's names — the same read ``daemon/api.py``'s
    ``_read_mcp_snapshot`` performs.  It NEVER re-probes a server.
    """
    try:
        from daemon.mcp import init_guard
        from daemon.mcp.builtin_servers import get_registry

        snapshot = init_guard.state_snapshot()
        for definition in get_registry().get_all():
            name = definition.name
            if name in snapshot:
                continue
            snapshot[name] = {
                "state": "ok",
                "reason": None,
                "since": None,
                "init_attempts": 0,
                "last_error_class": None,
                "breaker_state": None,
                "recoverable": True,
            }
        return snapshot
    except Exception:  # noqa: BLE001 — advisory only
        return None


#: The cached runtime bundle — keyed by the app state's identity.  The
#: bundle's CONTENTS are stable (the manager, the monitor, the census);
#: the mutable facts it exposes are read through CALLABLES (the
#: readiness composite) or read fresh per tick (the census snapshot).
_RUNTIME_CACHE: tuple[int, WiringSources] | None = None


def current_sources() -> WiringSources:
    """The bundle the adapters read from RIGHT NOW.

    The explicit bundle wins; otherwise the live app state is resolved
    (and CACHED by the state's identity — the bundle's members are
    stable, and the readiness composite is read through a callable so
    it is never stale).
    """
    if _BOUND_SOURCES is not None:
        return _BOUND_SOURCES
    global _RUNTIME_CACHE
    try:
        state = _locate_app_state()
        if state is None:
            return WiringSources()
        key = id(state)
        cached = _RUNTIME_CACHE
        if cached is not None and cached[0] == key:
            return cached[1]
        bundle = _build_runtime_sources(state)
        _RUNTIME_CACHE = (key, bundle)
        return bundle
    except Exception:  # noqa: BLE001 — a locator failure degrades, never raises
        logger.debug("metrics wiring: source locator failed", exc_info=True)
        return WiringSources()


__all__ = [
    "WiringSources",
    "bind_sources",
    "current_sources",
    "unbind_sources",
]
