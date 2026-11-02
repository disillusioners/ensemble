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

Mutation table: ``P3-M5`` (the listener start moved AFTER the census
block) · ``P3-M6`` (the sampler stop removed from the shutdown path) ·
``P3-M10`` (the sampler start moved before the listener start).
"""

from __future__ import annotations

import inspect

from daemon import api as daemon_api


def _lifespan_source() -> str:
    return inspect.getsource(inspect.unwrap(daemon_api.lifespan))


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
