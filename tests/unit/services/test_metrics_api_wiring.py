"""P0 T1 — the api.py LIFESPAN wiring (plan §4 T1 boot/teardown order).

The listener's boot/teardown placement is a PLAN requirement, not a
style choice:

* boot AFTER the resource monitor and the llm_stream_watchdog (A13);
* the import lives ON THE LIFESPAN PATH — never at daemon/api.py module
  top level (A27, the PyInstaller freeze rule);
* teardown AFTER the resource-monitor stop and the wedge-capture stop
  (R-m2).

These are asserted at the SOURCE level — booting the daemon is not a
unit test.
"""

from __future__ import annotations

import inspect

from daemon import api as daemon_api


def _lifespan_source() -> str:
    return inspect.getsource(inspect.unwrap(daemon_api.lifespan))


def test_the_listener_import_is_not_at_module_top_level():
    """A27 — the PyInstaller analyzer must see the import on the
    LIFESPAN path; a module-top-level import would be missed by the
    freezer's path analysis (and would drag FastAPI into a stdlib
    server module)."""
    module_source = inspect.getsource(daemon_api)
    head = module_source.split("async def lifespan", 1)[0]
    assert "from daemon.services.metrics_listener import" not in head
    assert "import daemon.services.metrics_listener" not in head


def test_the_listener_is_imported_on_the_lifespan_path():
    assert "from daemon.services.metrics_listener import" in _lifespan_source()


def test_the_census_is_imported_on_the_lifespan_path():
    assert "from daemon.services.metrics_census import" in _lifespan_source()


def test_the_listener_boots_after_the_resource_monitor():
    source = _lifespan_source()
    resource_monitor = source.index("build_resource_monitor")
    listener = source.index("MetricsListener(")
    assert resource_monitor < listener


def test_the_listener_boots_after_the_llm_stream_watchdog():
    source = _lifespan_source()
    watchdog = source.index("run_stream_watchdog_loop")
    listener = source.index("MetricsListener(")
    assert watchdog < listener


def test_the_listener_teardown_is_after_the_resource_monitor_stop():
    source = _lifespan_source()
    resource_monitor_stop = source.index("_resource_monitor.stop()")
    listener_stop = source.index("_metrics_listener.stop()")
    assert resource_monitor_stop < listener_stop


def test_the_listener_teardown_is_after_the_wedge_capture_stop():
    source = _lifespan_source()
    wedge_stop = source.index("_wedge_watchdog.stop()")
    listener_stop = source.index("_metrics_listener.stop()")
    assert wedge_stop < listener_stop


def test_the_census_teardown_is_after_the_listener_teardown():
    source = _lifespan_source()
    listener_stop = source.index("_metrics_listener.stop()")
    census_stop = source.index("_metrics_census.stop()")
    assert listener_stop < census_stop


def test_a_listener_boot_failure_does_not_break_the_lifespan():
    """A bind failure (or any listener boot failure) degrades to
    disabled + logged — the daemon CONTINUES."""
    source = _lifespan_source()
    assert "except Exception as metrics_listener_boot_exc:" in source
    assert "app.state.metrics_listener = None" in source


def test_the_registry_providers_are_wired_at_boot():
    source = _lifespan_source()
    assert "set_publisher_cadence_provider" in source
    assert "set_cardinality_cap_provider" in source
    assert "metrics_scrape_interval_hint_s" in source
    assert "metrics_cardinality_cap" in source


def test_the_f0_1_families_are_registered_at_boot():
    source = _lifespan_source()
    assert "ensure_f0_1_registered()" in source
