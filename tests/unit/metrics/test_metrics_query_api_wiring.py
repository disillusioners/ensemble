"""The query-door LIFESPAN wiring pack (registered pack
``metrics_query_api_wiring``).

The lifespan placement is a PLAN requirement, not a style choice
(AM-13):

* **start** — the metrics listener starts FIRST, the census block runs
  SECOND, and the sampler starts LAST of the trio (the census publishes
  into the registry, so the sampler must see a settled registry; the
  listener must never wait on either);
* **stop** — the sampler stops FIRST (it is the last thing that starts),
  before the listener stop;
* a construction/start failure DEGRADES (log + continue) — the daemon
  CONTINUES;
* the query provider is NOT a ``/readyz`` component (AM-24) — the
  readiness composite is parsed by the soak-gate grace discipline and
  the door must not grow that surface.

These are asserted at the SOURCE level — booting the daemon is not a
unit test (the ``test_metrics_api_wiring.py`` precedent).

IT ALSO CARRIES THE C-1 LOCATOR PINS — the runtime source bundle is
the substrate every family adapter reads, and the C-1 defect (a bundle
cached by ``id(app.state)``, frozen at the sampler's first tick BEFORE
the lifespan assigned the producer handles) lived exactly here.

Mutation table: ``P3-M5`` (the listener start moved AFTER the census
block) · ``P3-M6`` (the sampler stop removed from the shutdown path) ·
``P3-M10`` (the sampler start moved before the listener start).
"""

from __future__ import annotations

import inspect
import logging
import sys
import types
from typing import Any, Iterator

import pytest

from daemon import api as daemon_api
from daemon.services.metrics_query.wiring import sources as wiring_sources


def _lifespan_source() -> str:
    return inspect.getsource(inspect.unwrap(daemon_api.lifespan))


@pytest.fixture
def fake_daemon_api() -> Iterator[Any]:
    """Install a fake ``daemon.api`` module — the locator's only input.

    The locator reads ``sys.modules["daemon.api"].app.state`` (it never
    imports the module — a reverse import would be circular and would
    run ``create_app()`` twice).  The fixture restores whatever was
    there before, so the real module never leaks into another test.
    """
    saved = sys.modules.get("daemon.api")
    module = types.ModuleType("daemon.api")
    module.app = types.SimpleNamespace(state=types.SimpleNamespace())  # type: ignore[attr-defined]
    sys.modules["daemon.api"] = module
    wiring_sources._reset_runtime_probe()
    try:
        yield module
    finally:
        if saved is None:
            sys.modules.pop("daemon.api", None)
        else:
            sys.modules["daemon.api"] = saved
        wiring_sources._reset_runtime_probe()


class TestStartOrder:
    """listener → census → sampler (AM-13)."""

    def test_the_listener_starts_first(self):
        source = _lifespan_source()
        assert source.index("MetricsListener(") < source.index("CensusSampler(")

    def test_the_sampler_starts_last(self):
        source = _lifespan_source()
        assert source.index("CensusSampler(") < source.index("MetricsSampler(")

    def test_the_listener_start_precedes_the_sampler_start(self):
        source = _lifespan_source()
        assert source.index("MetricsListener(") < source.index("MetricsSampler(")

    def test_the_census_block_precedes_the_sampler_construction(self):
        """The census PUBLISHES into the registry — the sampler reads it."""
        source = _lifespan_source()
        assert source.index("_metrics_census.start()") < source.index(
            "MetricsSampler("
        )


class TestStopOrder:
    """sampler → listener → census (the inverse of the start order)."""

    def test_the_sampler_stops_before_the_listener(self):
        source = _lifespan_source()
        assert source.index("_metrics_sampler.stop()") < source.index(
            "_metrics_listener.stop()"
        )

    def test_the_sampler_stop_is_present(self):
        assert "_metrics_sampler.stop()" in _lifespan_source()

    def test_the_sampler_stop_is_detached(self):
        """The event loop is NEVER blocked on a join (house precedent)."""
        source = _lifespan_source()
        assert ".stop()" in source
        assert "join(" not in source.split("_metrics_sampler.stop()", 1)[1][:400]


class TestWiring:
    """The provider + sampler are wired and degrade honestly."""

    def test_the_provider_is_stored_on_the_app_state(self):
        source = _lifespan_source()
        assert "app.state.metrics_query = _metrics_query_provider" in source

    def test_the_sampler_is_stored_on_the_app_state(self):
        source = _lifespan_source()
        assert "app.state.metrics_sampler = _metrics_sampler" in source

    def test_a_boot_failure_degrades_to_disabled(self):
        source = _lifespan_source()
        assert "except Exception as metrics_query_boot_exc:" in source
        assert "app.state.metrics_query = None" in source
        assert "app.state.metrics_sampler = None" in source

    def test_the_imports_live_on_the_lifespan_path(self):
        """A27 — the PyInstaller analyzer must see the import on the
        LIFESPAN path, never at module top level."""
        module_source = inspect.getsource(daemon_api)
        head = module_source.split("async def lifespan", 1)[0]
        assert "from daemon.services.metrics_query import" not in head
        source = _lifespan_source()
        assert "from daemon.services.metrics_query import" in source


class TestNotAReadinessComponent:
    """AM-24 — the provider is NOT a ``/readyz`` component."""

    def test_the_readiness_component_names_do_not_include_the_door(self):
        from daemon.services.readiness import READINESS_COMPONENT_NAMES

        assert "metrics_query" not in READINESS_COMPONENT_NAMES
        assert not any(
            "metric" in name for name in READINESS_COMPONENT_NAMES
        ), (
            "the readiness composite's components are unchanged — the door "
            "must not grow the readiness surface"
        )

    def test_the_readiness_module_never_references_the_provider(self):
        from pathlib import Path

        path = (
            Path(daemon_api.__file__).resolve().parent / "services" / "readiness.py"
        )
        text = path.read_text(encoding="utf-8")
        assert "metrics_query" not in text


class TestTheRuntimeSourceLocator:
    """C-1 — the bundle must carry the LIVE handles, never a snapshot.

    THE DEFECT THIS PINS: the bundle was cached by ``id(app.state)``
    and built once, at the sampler's FIRST tick — which happens BEFORE
    the lifespan assigns ``app.state.manager``.  Because ``app.state``
    is never replaced, the stale bundle (every handle ``None``) was
    served for the life of the process, and 12 of the 16 admitted
    families were DARK in production.

    THE INVARIANT IS ABOUT THE HANDLES, NOT THE SERIES: a pin that
    asserted "the family has no series" would pass on the broken code
    (no handles ⇒ no series).  These pins assert HANDLE IDENTITY.
    """

    def test_a_late_assigned_manager_is_picked_up(self, fake_daemon_api):
        """The first tick sees no manager; the next tick must see it."""
        state = fake_daemon_api.app.state
        assert wiring_sources.current_sources().manager is None
        manager = object()
        state.manager = manager
        assert wiring_sources.current_sources().manager is manager

    def test_a_replaced_manager_is_picked_up(self, fake_daemon_api):
        """A REPLACED handle must be visible on the next resolution."""
        state = fake_daemon_api.app.state
        first = object()
        state.manager = first
        assert wiring_sources.current_sources().manager is first
        second = object()
        state.manager = second
        assert wiring_sources.current_sources().manager is second

    def test_a_replaced_app_state_is_picked_up(self, fake_daemon_api):
        """A REPLACED ``app.state`` must be visible on the next read."""
        first = object()
        fake_daemon_api.app.state = types.SimpleNamespace(manager=first)
        assert wiring_sources.current_sources().manager is first
        second = object()
        fake_daemon_api.app.state = types.SimpleNamespace(manager=second)
        assert wiring_sources.current_sources().manager is second

    def test_the_dsn_appears_with_the_engine(self, fake_daemon_api):
        """The DSN is the handle 7 of the 10 dark families depended on."""

        class _Url:
            def render_as_string(self, hide_password: bool = True) -> str:
                return "postgresql://u:p@localhost:5432/ensemble_dev"

        class _Engine:
            url = _Url()

        class _Manager:
            engine = _Engine()

        assert wiring_sources.current_sources().dsn is None
        fake_daemon_api.app.state.manager = _Manager()
        dsn = wiring_sources.current_sources().dsn
        assert dsn is not None and "ensemble_dev" in dsn

    def test_the_absence_never_poisons_the_next_resolution(
        self, fake_daemon_api
    ):
        """C-1 ③ — the NEGATIVE CONTROL.

        With no ``daemon.api`` in ``sys.modules`` the bundle is EMPTY —
        and the emptiness must not be remembered: the next resolution
        must carry the real handles.  Asserted on HANDLE IDENTITY, never
        on "the family has no series" (which the broken code also
        satisfied).
        """
        module = sys.modules.pop("daemon.api")
        try:
            empty = wiring_sources.current_sources()
            assert empty.manager is None
            assert empty.dsn is None
            assert empty.sweeps == {}
        finally:
            sys.modules["daemon.api"] = module
        # …and the bundle is NOT poisoned:
        manager = object()
        fake_daemon_api.app.state.manager = manager
        assert wiring_sources.current_sources().manager is manager

    def test_the_probe_logs_the_resolved_handles(self, fake_daemon_api, caplog):
        """C-1 ⑤ — the runtime probe logs the bundle's handle presence."""
        fake_daemon_api.app.state.manager = object()
        with caplog.at_level(
            logging.INFO, logger="daemon.services.metrics_query.wiring.sources"
        ):
            wiring_sources.current_sources()
        lines = [
            record.getMessage()
            for record in caplog.records
            if "runtime source bundle resolved" in record.getMessage()
        ]
        assert lines, "the probe must log the resolved bundle"
        present = lines[0].split("present=", 1)[1].split(" absent=", 1)[0]
        assert "manager" in present.split(","), present
        absent = lines[0].split(" absent=", 1)[1]
        assert "dsn" in absent.split(","), absent

    def test_the_absent_locator_says_so(self, caplog):
        """A-8 — the "no daemon.api" fallback is never SILENT."""
        module = sys.modules.pop("daemon.api", None)
        try:
            with caplog.at_level(
                logging.INFO,
                logger="daemon.services.metrics_query.wiring.sources",
            ):
                wiring_sources.current_sources()
        finally:
            if module is not None:
                sys.modules["daemon.api"] = module
        lines = [
            record.getMessage()
            for record in caplog.records
            if "runtime source locator" in record.getMessage()
        ]
        assert lines, "the absent locator must be logged"

    def test_the_explicit_bundle_still_wins(self, fake_daemon_api):
        """The explicit override is unchanged by the C-1 fix."""
        fake_daemon_api.app.state.manager = object()
        explicit = wiring_sources.WiringSources(manager="explicit")
        wiring_sources.bind_sources(explicit)
        try:
            assert wiring_sources.current_sources() is explicit
        finally:
            wiring_sources.unbind_sources()
