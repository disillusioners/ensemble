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
series", which is the honest absent-≠-zero posture.  That absence is
LOGGED (once per process) — a silent "no series" with no word would be
unreadable in a live log (A-8).

NO CONTENT CACHE — THE BUNDLE IS REBUILT ON EVERY RESOLUTION
-------------------------------------------------------------
An earlier revision cached the bundle's CONTENTS under ``id(app.state)``
and served the cached bundle on every later resolution.  That was
WRONG, and the failure was PERMANENT rather than a race: the sampler
starts BEFORE the lifespan assigns the producer handles
(``daemon/api.py`` starts the sampler ~200 lines before
``app.state.manager`` is assigned), so the FIRST resolution froze a
bundle of ``None`` handles — and because ``app.state`` is never
replaced, ``id(app.state)`` never changed and the stale bundle was
served for the life of the process.

THE BLAST RADIUS WAS TWELVE OF THE SIXTEEN ADMITTED FAMILIES: seven of
them were dark SOLELY because ``dsn`` stayed ``None`` (the DB-backed
adapters skip their queries when there is no DSN), and the rest were
dark because their producer handle was ``None``.  Only F6, F7, F9 and
F21 stayed live.  NOTE THE FAILURE MODE: there was NO ambient-DSN
exposure — ``resolve_dsn`` is called ONLY inside ``if engine is not
None``, so the DSN stayed ``None`` and the adapters skipped their
queries entirely.  The families were SILENTLY ABSENT, not wrong.

So the bundle is rebuilt on EVERY resolution.  The cost is a handful of
attribute reads plus one ``engine.url.render_as_string()`` —
microseconds at the sampler's 10 s cadence — and the price of that
microsecond is never serving a stale handle again.  There is NO
content cache and NO identity key.

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


def unbind_sources() -> None:
    """Restore the runtime locator (the explicit bundle is dropped)."""
    bind_sources(None)


def _locate_app() -> Any | None:
    """The live FastAPI app — WITHOUT importing ``daemon.api``.

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
    return app


def _locate_app_state() -> Any | None:
    """The live app's ``state`` — WITHOUT importing ``daemon.api``.

    The state is resolved FRESH on every call: the app's ``state``
    object is never replaced by the daemon, but a REPLACEMENT must be
    visible to the adapters all the same (the C-1 invariant).
    """
    app = _locate_app()
    if app is None:
        return None
    return getattr(app, "state", None)


def _read_app_handle(app: Any, name: str) -> Any:
    """Read a handle off the app's CURRENT state.

    The state object is resolved at CALL time, not captured at build
    time: a REPLACED ``app.state`` must be visible to the callable
    handles exactly like a replaced handle is.
    """
    state = getattr(app, "state", None)
    if state is None:
        return None
    return getattr(state, name, None)


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


def _build_runtime_sources(app: Any) -> WiringSources:
    """Build the bundle from the live app state.

    The bundle is built FRESH on every resolution (no content cache):
    the producer handles are assigned ACROSS the lifespan, so a cached
    bundle would freeze the handles that happened to exist at the first
    resolution — and the first resolution happens at the sampler's first
    tick, before the lifespan has assigned any of them.
    """
    state = getattr(app, "state", None)
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
        readiness_composite=lambda: _read_app_handle(app, "readiness_composite"),
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


#: The runtime probe — the resolved bundle's handle PRESENCE, logged the
#: FIRST time a bundle is resolved and again whenever the presence
#: CHANGES.  It is the C-1 ⑤ probe: a daemon-side log line that shows
#: which producer handles the locator actually resolved, so the
#: dark→live transition is OBSERVABLE in a live log instead of being
#: inferred from a static read of the source.
_PROBE_PRESENCE: dict[str, bool] | None = None

#: The locator-absence latch — the "no ``daemon.api`` in this process"
#: line is emitted ONCE per process (A-8: the fallback was SILENT).
_ABSENCE_LOGGED: bool = False


def _install_dir_present(bundle: WiringSources) -> bool:
    """Whether the bundle's install dir RESOLVES to a path (ND-5).

    The bundle carries the RESOLVER CALLABLE (the import is deferred
    to the tick that needs it), so the CALLABLE's presence says nothing
    about whether an install dir EXISTS: the callable is always there,
    while the resolved path is ``None`` in dev and in every lane with
    no staged install.  The probe must report the RESOLVED path's
    presence — otherwise the presence map claims an install dir that
    does not exist.
    """
    resolver = bundle.install_dir
    if resolver is None:
        return False
    try:
        return resolver() is not None
    except Exception:  # noqa: BLE001 — a resolver failure is "no install dir"
        return False


def bundle_handle_presence(bundle: WiringSources) -> dict[str, bool]:
    """The bundle's handle-presence map (the probe's payload).

    The map is about HANDLES, never about series: a handle that is
    present but has published nothing is still PRESENT (absent ≠ zero).

    ONE EXCEPTION, and it is the truth (ND-5): ``install_dir`` reports
    the RESOLVED PATH's presence, not the resolver callable's — the
    callable is a handle that is ALWAYS there, while the install dir
    itself may not exist.
    """
    return {
        "manager": bundle.manager is not None,
        "resource_monitor": bundle.resource_monitor is not None,
        "wedge_watchdog": bundle.wedge_watchdog is not None,
        "census": bundle.census is not None,
        "readiness_composite": bundle.readiness_composite is not None,
        "mcp_snapshot": bundle.mcp_snapshot is not None,
        "mcps_gateway_service": bundle.mcps_gateway_service is not None,
        "sweeps": bool(bundle.sweeps),
        "install_dir": _install_dir_present(bundle),
        "dsn": bundle.dsn is not None,
        "db_connect": bundle.db_connect is not None,
    }


def _emit_runtime_probe(bundle: WiringSources) -> None:
    """Log the resolved bundle's handles (once, then on every CHANGE)."""
    global _PROBE_PRESENCE
    presence = bundle_handle_presence(bundle)
    if _PROBE_PRESENCE is not None and presence == _PROBE_PRESENCE:
        return
    _PROBE_PRESENCE = presence
    present = sorted(name for name, live in presence.items() if live)
    absent = sorted(name for name, live in presence.items() if not live)
    logger.info(
        "[Metrics] runtime source bundle resolved: present=%s absent=%s",
        ",".join(present) or "none",
        ",".join(absent) or "none",
    )


def _emit_locator_absence() -> None:
    """Say ONCE that this process has no ``daemon.api`` to resolve from."""
    global _ABSENCE_LOGGED
    if _ABSENCE_LOGGED:
        return
    _ABSENCE_LOGGED = True
    logger.info(
        "[Metrics] runtime source locator: daemon.api is not in "
        "sys.modules — the pull adapters have no producer handles "
        "(every family renders no series; absent is not zero)"
    )


def _reset_runtime_probe() -> None:
    """Reset the probe/absence latches (the pin's fixture hook)."""
    global _PROBE_PRESENCE, _ABSENCE_LOGGED
    _PROBE_PRESENCE = None
    _ABSENCE_LOGGED = False


def current_sources() -> WiringSources:
    """The bundle the adapters read from RIGHT NOW.

    The explicit bundle wins; otherwise the live app state is resolved
    FRESH — there is NO content cache (see the module docstring: the
    cached bundle froze the ``None`` handles that existed at the
    sampler's first tick, and the stale bundle was PERMANENT).
    """
    if _BOUND_SOURCES is not None:
        return _BOUND_SOURCES
    try:
        app = _locate_app()
        if app is None:
            _emit_locator_absence()
            return WiringSources()
        bundle = _build_runtime_sources(app)
        _emit_runtime_probe(bundle)
        return bundle
    except Exception:  # noqa: BLE001 — a locator failure degrades, never raises
        logger.debug("metrics wiring: source locator failed", exc_info=True)
        return WiringSources()


__all__ = [
    "WiringSources",
    "bind_sources",
    "bundle_handle_presence",
    "current_sources",
    "unbind_sources",
]
