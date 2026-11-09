"""The query provider pack (registered pack ``metrics_query_provider``).

The provider is the door's ONLY read substrate, so its pins are the
load-bearing ones:

* it reads PUBLISHED state only — never a metric lock, never the
  exposition renderer, never a mutation call, never a database;
* it serves the canonical frozen family shape;
* the family metadata is discoverable at REGISTRATION (AM-3) — the 18
  pre-existing registrations keep their DERIVED defaults;
* ``buckets`` is ON the wire (B3);
* ``census_gated`` comes from the PUBLIC ``CensusSampler.gated``
  property — the query package NEVER reads the switch (AM-18), and the
  behavioural check-site census over ``daemon/`` stays at exactly 2.

Mutation table: ``P2-M1`` (provider reads a live lock) · ``P2-M2``
(provider calls ``render_metrics()``) · ``P2-M12``
(``last_publish_age_s`` read from the private ``_last_publish``) ·
``P2-M13`` (the public ``CensusSampler.gated`` property removed, or a
direct switch read in the query package) · ``P2-M14`` (the
family-metadata kwargs made mandatory) · ``P2-M15`` (``buckets``
dropped from the name shape).
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import pytest

from daemon import checkpoint_metrics as cm
from daemon.services import metrics_query as mq
from daemon.services.metrics_query.provider import RegistryMetricsQueryProvider

PACKAGE_DIR = Path(mq.__file__).resolve().parent


def _package_sources() -> list[Path]:
    return sorted(PACKAGE_DIR.glob("*.py"))


def _package_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in _package_sources())


def _provider_source() -> str:
    return (PACKAGE_DIR / "provider.py").read_text(encoding="utf-8")


def _metric(
    name: str,
    kind: str = "gauge",
    *,
    label_keys=(),
    allowed_values=None,
    buckets=None,
    help: str = "",
    **metadata,
) -> cm.Metric:
    """A real (UNREGISTERED) metric object — no global registry pollution."""
    return cm.Metric(
        name,
        kind,
        label_keys,
        allowed_values,
        help,
        buckets,
        False,
        **metadata,
    )


def _published(metric: cm.Metric, value: float = 0.0, labels=None) -> cm.Metric:
    metric.set(value, labels)
    metric.publish(force=True)
    return metric


class TestReadPathPurity:
    """The load-bearing read contract."""

    def test_the_provider_never_calls_the_exposition_renderer(self) -> None:
        assert "render_metrics" not in _provider_source()

    def test_the_provider_never_reaches_for_a_metric_lock(self) -> None:
        assert re.search(r"\.lock\b", _provider_source()) is None

    def test_the_provider_never_mutates_the_registry(self) -> None:
        source = _provider_source()
        for forbidden in (".inc(", ".observe(", ".set("):
            assert forbidden not in source, f"the provider must not call {forbidden}"

    def test_the_provider_never_reaches_for_the_private_publish_field(self) -> None:
        assert "_last_publish" not in _provider_source()

    def test_the_package_never_reads_the_kill_switch(self) -> None:
        text = _package_text()
        for forbidden in ("metrics_gate", "ENSEMBLE_METRICS_ENABLED", "metrics_enabled("):
            assert forbidden not in text, f"the query package must not read {forbidden}"

    def test_the_package_imports_no_database_client(self) -> None:
        text = _package_text()
        for forbidden in ("psycopg", "sqlalchemy", "create_engine", "Session"):
            assert forbidden not in text, f"the query package must not import {forbidden}"

    def test_the_package_registers_no_metric_of_its_own(self) -> None:
        text = _package_text()
        assert "register_counter" not in text
        assert "register_gauge" not in text
        assert "register_histogram" not in text

    def test_the_check_site_census_over_daemon_stays_at_two(self) -> None:
        """The switch is consulted at exactly two behavioural sites."""
        repo = Path(__file__).resolve().parents[3]
        hits: list[str] = []
        for path in sorted((repo / "daemon").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), start=1):
                if "metrics_gate.metrics_enabled()" in line:
                    hits.append(f"{path.relative_to(repo)}:{number}")
        assert len(hits) == 2, f"the behavioural check-site census drifted: {hits}"

    def test_the_census_sampler_exposes_the_public_gated_property(self) -> None:
        from daemon.services.metrics_census import CensusSampler

        assert hasattr(CensusSampler, "gated")
        assert hasattr(CensusSampler, "_gate")


class TestFamilyShape:
    """The canonical frozen family shape."""

    def test_the_family_entry_carries_the_frozen_members(self) -> None:
        metric = _published(_metric("ensemble_provider_shape_gauge", help="shape"))
        provider = RegistryMetricsQueryProvider(metrics_provider=lambda: [metric])
        family = provider.families()[0]
        assert set(family) == {
            "id",
            "names",
            "live_series_count",
            "availability",
            "role",
            "source_idiom",
            "durability",
            "cardinality_contribution",
        }

    def test_the_name_entry_carries_the_frozen_members(self) -> None:
        metric = _published(_metric("ensemble_provider_name_shape_gauge"))
        provider = RegistryMetricsQueryProvider(metrics_provider=lambda: [metric])
        entry = provider.families()[0]["names"][0]
        assert set(entry) == {
            "name",
            "kind",
            "labels",
            "help",
            "buckets",
            "cardinality_class",
            "monotonic",
            "chart_role",
        }

    def test_buckets_are_on_the_wire(self) -> None:
        """B3 — the deferred consumers need the bucket sets off-box."""
        metric = _published(
            _metric(
                "ensemble_provider_bucket_histogram",
                "histogram",
                buckets=(1.0, 5.0, 10.0),
            )
        )
        provider = RegistryMetricsQueryProvider(metrics_provider=lambda: [metric])
        entry = provider.families()[0]["names"][0]
        assert entry["buckets"] == [1.0, 5.0, 10.0]

    def test_a_non_histogram_carries_no_buckets(self) -> None:
        metric = _published(_metric("ensemble_provider_no_bucket_gauge"))
        provider = RegistryMetricsQueryProvider(metrics_provider=lambda: [metric])
        assert provider.families()[0]["names"][0]["buckets"] is None

    def test_the_labels_are_the_declared_value_sets(self) -> None:
        metric = _published(
            _metric(
                "ensemble_provider_closed_gauge",
                label_keys=("state",),
                allowed_values={"state": frozenset({"a", "b"})},
            )
        )
        provider = RegistryMetricsQueryProvider(metrics_provider=lambda: [metric])
        entry = provider.families()[0]["names"][0]
        assert entry["labels"] == {"state": ["a", "b"]}
        assert entry["cardinality_class"] == "closed"

    def test_an_open_metric_renders_no_labels(self) -> None:
        metric = _published(
            _metric("ensemble_provider_open_gauge", label_keys=("metric",))
        )
        provider = RegistryMetricsQueryProvider(metrics_provider=lambda: [metric])
        entry = provider.families()[0]["names"][0]
        assert entry["labels"] is None
        assert entry["cardinality_class"] == "open"

    def test_a_counter_is_monotonic_and_a_gauge_is_not(self) -> None:
        counter = _published(_metric("ensemble_provider_monotonic_counter", "counter"))
        gauge = _published(_metric("ensemble_provider_nonmonotonic_gauge"))
        provider = RegistryMetricsQueryProvider(
            metrics_provider=lambda: [counter, gauge]
        )
        by_name = {
            entry["name"]: entry
            for family in provider.families()
            for entry in family["names"]
        }
        assert by_name["ensemble_provider_monotonic_counter"]["monotonic"] is True
        assert by_name["ensemble_provider_nonmonotonic_gauge"]["monotonic"] is False

    def test_the_summary_kind_never_appears(self) -> None:
        """AM-4 — the registry never produces a ``summary``."""
        metric = _published(_metric("ensemble_provider_kind_gauge"))
        provider = RegistryMetricsQueryProvider(metrics_provider=lambda: [metric])
        assert provider.families()[0]["names"][0]["kind"] == "gauge"
        assert "summary" not in {m.kind for m in cm.registered_metrics()}


class TestFamilyMetadataAtRegistration:
    """AM-3 — the family metadata lives at registration."""

    def test_the_metadata_kwargs_are_optional(self) -> None:
        """P2-M14 — the 18 pre-existing registrations stay untouched."""
        metric = cm.register_gauge("ensemble_provider_metadata_default_gauge")
        assert metric.family == "ensemble_provider_metadata_default_gauge"
        assert metric.source_idiom == "registry"
        assert metric.durability == "process"
        assert metric.cardinality_weight == 1
        assert metric.chart_role is None
        assert metric.is_chart_primary() is False

    def test_an_explicit_family_is_served_without_a_door_edit(self) -> None:
        metric = _published(
            _metric(
                "ensemble_provider_family_gauge",
                family="F99",
                source_idiom="registry",
                durability="process",
                cardinality_weight=2,
                chart_role="line",
            )
        )
        provider = RegistryMetricsQueryProvider(metrics_provider=lambda: [metric])
        family = provider.families()[0]
        assert family["id"] == "F99"
        assert family["role"] == cm.FAMILY_ROLE_CHART
        assert family["source_idiom"] == "registry"
        assert family["durability"] == "process"

    def test_a_declared_chart_role_makes_the_family_chart_primary(self) -> None:
        metric = _published(
            _metric("ensemble_provider_chart_gauge", chart_role="step")
        )
        provider = RegistryMetricsQueryProvider(metrics_provider=lambda: [metric])
        family = provider.families()[0]
        assert family["role"] == cm.FAMILY_ROLE_CHART
        assert family["names"][0]["chart_role"] == "step"

    def test_an_undeclared_family_is_stat_primary(self) -> None:
        metric = _published(_metric("ensemble_provider_stat_gauge"))
        provider = RegistryMetricsQueryProvider(metrics_provider=lambda: [metric])
        assert provider.families()[0]["role"] == cm.FAMILY_ROLE_STAT

    def test_the_cardinality_contribution_is_weighted(self) -> None:
        metric = _metric(
            "ensemble_provider_weighted_gauge",
            label_keys=("k",),
            allowed_values={"k": frozenset({"a", "b"})},
            cardinality_weight=3,
        )
        metric.set(1, {"k": "a"})
        metric.set(2, {"k": "b"})
        metric.publish(force=True)
        provider = RegistryMetricsQueryProvider(metrics_provider=lambda: [metric])
        family = provider.families()[0]
        assert family["live_series_count"] == 2
        assert family["cardinality_contribution"] == 6

    def test_an_unknown_source_idiom_is_rejected_at_registration(self) -> None:
        with pytest.raises(ValueError):
            cm.register_gauge(
                "ensemble_provider_bad_idiom_gauge", source_idiom="nope"
            )

    def test_an_unknown_chart_role_is_rejected_at_registration(self) -> None:
        with pytest.raises(ValueError):
            cm.register_gauge(
                "ensemble_provider_bad_role_gauge", chart_role="nope"
            )

    def test_a_negative_cardinality_weight_is_rejected_at_registration(self) -> None:
        with pytest.raises(ValueError):
            cm.register_gauge(
                "ensemble_provider_bad_weight_gauge", cardinality_weight=-1
            )


class TestAvailability:
    def test_a_family_without_an_observation_is_dark_with_a_reason(self) -> None:
        metric = _metric("ensemble_provider_absent_gauge")
        provider = RegistryMetricsQueryProvider(metrics_provider=lambda: [metric])
        availability = provider.families()[0]["availability"]
        assert availability == {"state": "dark", "reason": "series_absent"}

    def test_a_census_family_is_dark_when_the_census_is_gated(self) -> None:
        metric = _published(
            _metric("ensemble_provider_census_gauge", source_idiom="postgres_census")
        )

        class _GatedCensus:
            gated = True
            running = True

        provider = RegistryMetricsQueryProvider(
            census=_GatedCensus(), metrics_provider=lambda: [metric]
        )
        availability = provider.families()[0]["availability"]
        assert availability == {"state": "dark", "reason": "census_gated"}

    def test_a_census_family_is_dark_when_the_census_is_not_running(self) -> None:
        metric = _published(
            _metric("ensemble_provider_census_idle_gauge", source_idiom="postgres_census")
        )

        class _IdleCensus:
            gated = False
            running = False

        provider = RegistryMetricsQueryProvider(
            census=_IdleCensus(), metrics_provider=lambda: [metric]
        )
        availability = provider.families()[0]["availability"]
        assert availability == {"state": "dark", "reason": "census_not_running"}

    def test_a_capture_family_is_dark_when_the_capture_is_off(self) -> None:
        metric = _published(
            _metric("ensemble_provider_capture_gauge", source_idiom="wedge_capture")
        )
        provider = RegistryMetricsQueryProvider(
            capture_active=False, metrics_provider=lambda: [metric]
        )
        availability = provider.families()[0]["availability"]
        assert availability == {"state": "dark", "reason": "capture_off"}

    def test_a_live_family_reports_no_reason(self) -> None:
        metric = _published(_metric("ensemble_provider_live_gauge"))
        provider = RegistryMetricsQueryProvider(metrics_provider=lambda: [metric])
        assert provider.families()[0]["availability"] == {"state": "live"}


class TestAvailabilityOrderIsLaneOwned:
    """ND-micro — the ORDER property, pinned against the REAL class.

    The availability model must check `not census_running` BEFORE
    `census_gated`: a sampler that is not running reports
    `census_not_running` (the fact that explains it), regardless of
    the gate state.  The census's `gated` property is a GATED-OFF fact
    (R-ii, fixed at its fact owner — `metrics_census.py` now negates
    the enabled-sense `_gate()`), so `gated=True` here means the census
    is gated OFF.  This pin deliberately asserts ONLY the ORDER (the
    lane-owned property); the plan is that the query package keeps
    reading the public `CensusSampler.gated` (the two-check-sites
    invariant; AM-13/AM-18/D-11b).

    Uses the REAL `daemon.services.metrics_census.CensusSampler`
    (constructed but NOT started, so `running` is False) — not a fake —
    so the class-level behaviour is structurally visible.  The sampler's
    `gate_enabled` is seeded False (i.e. the census is ENABLED) so that
    `gated` is False; if the order were reversed the enabled+running
    state would be moot for a non-running sampler, but the point is that
    `census_not_running` must win regardless.  That is the
    discrimination.
    """

    def test_a_real_non_running_census_reports_not_running_not_gated(self) -> None:
        from daemon.services.metrics_census import CensusSampler

        metric = _metric(
            "ensemble_nd_micro_census_gauge", source_idiom="postgres_census"
        )
        # gate_enabled=False ⇒ the census is ENABLED ⇒ gated=False (R-ii:
        # `gated` is the GATED-OFF sense); NOT started ⇒ running=False.
        # The ORDER property must still win.
        sampler = CensusSampler(gate_enabled=lambda: False, dsn=None)
        assert sampler.running is False
        provider = RegistryMetricsQueryProvider(
            census=sampler, metrics_provider=lambda: [metric]
        )
        availability = provider.families()[0]["availability"]
        assert availability == {"state": "dark", "reason": "census_not_running"}, availability

    def test_a_real_running_census_reports_the_gate_state(self) -> None:
        """Control: a running, ENABLED census is not gated (R-ii, post-fix).

        R-ii is FIXED at the fact owner: `CensusSampler.gated` now
        returns `not self._gate()`, so `gate_enabled=lambda: True`
        (metrics gate ON ⇒ census ENABLED) yields `gated=False` and the
        family resolves on its SERIES, not on a false `census_gated`.
        That is the whole point of the fix: before it, this state
        reported `dark/census_gated` while 15 series published.
        """
        from daemon.services.metrics_census import CensusSampler

        metric = _published(
            _metric(
                "ensemble_nd_micro_census_gauge_2",
                source_idiom="postgres_census",
            )
        )
        sampler = CensusSampler(gate_enabled=lambda: True, dsn=None)
        # Mark it running without starting the thread (the property is a
        # plain boolean attribute set by start()).
        sampler._started = True
        assert sampler.gated is False, (
            "R-ii: an ENABLED census must not report itself as gated"
        )
        provider = RegistryMetricsQueryProvider(
            census=sampler, metrics_provider=lambda: [metric]
        )
        availability = provider.families()[0]["availability"]
        assert availability == {"state": "live"}, availability

    def test_a_real_running_but_gated_census_still_reports_census_gated(self) -> None:
        """The refusal direction is preserved (R-ii's negative arm).

        A census whose gate is OFF is genuinely gated: `_gate()` is
        False ⇒ `gated` is True ⇒ `census_gated` is the honest reason.
        The polarity fix must not turn a real gate into a false `live`.
        """
        from daemon.services.metrics_census import CensusSampler

        metric = _published(
            _metric(
                "ensemble_nd_micro_census_gauge_3",
                source_idiom="postgres_census",
            )
        )
        sampler = CensusSampler(gate_enabled=lambda: False, dsn=None)
        sampler._started = True
        assert sampler.gated is True
        provider = RegistryMetricsQueryProvider(
            census=sampler, metrics_provider=lambda: [metric]
        )
        availability = provider.families()[0]["availability"]
        assert availability == {"state": "dark", "reason": "census_gated"}, availability

    def test_every_emitted_reason_is_inside_the_closed_v1_set(self) -> None:
        """A-9 — the BACKEND-side reason-set assertion.

        The frontend widens its availability union with an
        ``unknown(<value>)`` fallback, so a NEW reason would be
        INVISIBLE there: the frontend would render a reason nobody can
        match, and the reason would never be observed.  The backend
        therefore refuses to emit a reason outside the closed v1 set.
        """
        from daemon.services.metrics_query.availability import AVAILABILITY_REASONS

        emitted: set[str] = set()
        for metric in (
            _metric("ensemble_provider_reason_absent_gauge"),
            _published(
                _metric(
                    "ensemble_provider_reason_census_gauge",
                    source_idiom="postgres_census",
                )
            ),
            _published(
                _metric(
                    "ensemble_provider_reason_capture_gauge",
                    source_idiom="wedge_capture",
                )
            ),
        ):
            provider = RegistryMetricsQueryProvider(
                capture_active=False, metrics_provider=lambda m=metric: [m]
            )
            reason = provider.families()[0]["availability"].get("reason")
            if reason is not None:
                emitted.add(reason)
        assert emitted, "the legs must have produced at least one reason"
        assert emitted <= set(AVAILABILITY_REASONS), emitted - set(AVAILABILITY_REASONS)

    def test_a_reason_outside_the_v1_set_is_refused(self) -> None:
        """A-9 — the assertion is REAL: a foreign reason does not pass.

        The availability model is monkeypatched to emit a reason that is
        not in the v1 set; the provider must refuse it (the assertion
        fires) instead of serving an unobservable reason.
        """
        from daemon.services.metrics_query import provider as provider_module

        metric = _metric("ensemble_provider_foreign_reason_gauge")
        original = provider_module.derive_availability

        def _foreign(**kwargs):
            return {"state": "dark", "reason": "not_a_v1_reason"}

        provider_module.derive_availability = _foreign  # type: ignore[assignment]
        try:
            provider = RegistryMetricsQueryProvider(metrics_provider=lambda: [metric])
            with pytest.raises(AssertionError):
                provider.families()
        finally:
            provider_module.derive_availability = original  # type: ignore[assignment]


class TestSnapshot:
    def test_the_snapshot_carries_the_staleness_metadata(self) -> None:
        metric = _published(_metric("ensemble_provider_snapshot_gauge"))
        provider = RegistryMetricsQueryProvider(metrics_provider=lambda: [metric])
        entry = provider.snapshot()[0]
        assert entry["name"] == "ensemble_provider_snapshot_gauge"
        assert entry["value"] == 0.0
        assert entry["last_publish_age_s"] >= 0.0
        assert entry["monotonic"] is False

    def test_a_histogram_carries_its_as_stored_block(self) -> None:
        metric = _published(
            _metric("ensemble_provider_snapshot_histogram", "histogram")
        )
        metric.observe(2.0)
        metric.publish(force=True)
        provider = RegistryMetricsQueryProvider(metrics_provider=lambda: [metric])
        entry = provider.snapshot()[0]
        assert entry["value"] is None
        assert entry["histogram"]["count"] == 1
        assert entry["histogram"]["sum"] == 2.0

    def test_the_family_filter_selects_one_family(self) -> None:
        first = _published(_metric("ensemble_provider_filter_a", family="A"))
        second = _published(_metric("ensemble_provider_filter_b", family="B"))
        provider = RegistryMetricsQueryProvider(
            metrics_provider=lambda: [first, second]
        )
        assert [e["name"] for e in provider.snapshot(family="A")] == [
            "ensemble_provider_filter_a"
        ]

    def test_the_name_prefix_filter_narrows_the_set(self) -> None:
        first = _published(_metric("ensemble_provider_prefix_one"))
        second = _published(_metric("ensemble_other_prefix_two"))
        provider = RegistryMetricsQueryProvider(
            metrics_provider=lambda: [first, second]
        )
        assert [e["name"] for e in provider.snapshot(name_prefix="ensemble_provider_")] == [
            "ensemble_provider_prefix_one"
        ]


class TestContention:
    def test_a_wedged_writer_cannot_block_the_provider(self) -> None:
        """The provider takes no metric lock — a wedged writer cannot stall it."""
        metric = _published(_metric("ensemble_provider_wedged_gauge"))
        provider = RegistryMetricsQueryProvider(metrics_provider=lambda: [metric])
        held = threading.Event()
        release = threading.Event()

        def _wedge() -> None:
            held.set()
            metric.lock.acquire()
            release.wait(5.0)
            metric.lock.release()

        worker = threading.Thread(target=_wedge, daemon=True)
        worker.start()
        try:
            assert held.wait(2.0)
            started = time.monotonic()
            assert provider.snapshot()
            assert provider.families()
            elapsed = time.monotonic() - started
            assert elapsed < 1.0, (
                f"the provider must not wait on a metric lock; it took {elapsed:.3f}s"
            )
        finally:
            release.set()
            worker.join(2.0)


class TestProtocolConformance:
    def test_a_duck_typed_fake_satisfies_the_protocol(self) -> None:
        class _Fake:
            # GAP-3 of the seam-side Protocol amendment — the trimmed
            # Protocol declares exactly ONE new member (``registry_read_ok``);
            # GAP-4/GAP-5 are NOT declared, so they cost nothing here.
            registry_read_ok = True

            def families(self):
                return []

            def snapshot(self, *, family=None, name_prefix=None):
                return []

            def history(self, **kwargs):
                return []

        assert isinstance(_Fake(), mq.MetricsQueryProvider)

    def test_the_registry_provider_satisfies_the_protocol(self) -> None:
        assert isinstance(
            RegistryMetricsQueryProvider(), mq.MetricsQueryProvider
        )

    def test_the_protocol_module_imports_no_web_framework(self) -> None:
        source = (PACKAGE_DIR / "protocol.py").read_text(encoding="utf-8")
        for forbidden in (
            "import fastapi",
            "from fastapi",
            "import starlette",
            "from starlette",
        ):
            assert forbidden not in source


class TestPackageIsImportSideEffectFree:
    """Plan task 2.1 — importing the package starts no thread, opens no
    connection and registers no metric."""

    def test_the_import_starts_no_thread_and_registers_no_metric(self) -> None:
        import subprocess
        import sys

        repo = Path(__file__).resolve().parents[3]
        script = (
            "import threading\n"
            "import daemon.services as services\n"
            "import daemon.checkpoint_metrics as cm\n"
            "before_threads = threading.active_count()\n"
            "before_metrics = len(cm.registered_metrics())\n"
            "import daemon.services.metrics_query  # noqa: F401\n"
            "after_threads = threading.active_count()\n"
            "after_metrics = len(cm.registered_metrics())\n"
            "assert after_threads == before_threads, (before_threads, after_threads)\n"
            "assert after_metrics == before_metrics, (before_metrics, after_metrics)\n"
            "print('OK')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "OK" in result.stdout


class TestEnvelope:
    """Plan task 2.9 — the envelope builders implement the frozen contract."""

    def test_the_header_carries_the_frozen_members(self) -> None:
        from daemon.services.metrics_query.envelope import envelope_header

        header = envelope_header(RegistryMetricsQueryProvider())
        assert set(header) == {
            "schema_version",
            "vocabulary_version",
            "generated_at_utc",
            "restart_epoch",
            "activation",
        }

    def test_the_generated_at_is_aware_utc(self) -> None:
        from daemon.services.metrics_query.envelope import envelope_header

        header = envelope_header(RegistryMetricsQueryProvider())
        assert header["generated_at_utc"].endswith("+00:00")

    def test_the_restart_epoch_is_the_boot_id(self) -> None:
        from daemon.services.bash_spawn_reaper import CURRENT_BOOT_ID
        from daemon.services.metrics_query.envelope import envelope_header

        header = envelope_header(RegistryMetricsQueryProvider())
        assert header["restart_epoch"] == CURRENT_BOOT_ID

    def test_the_activation_block_names_the_three_surfaces(self) -> None:
        from daemon.services.metrics_query.envelope import activation_block

        activation = activation_block(RegistryMetricsQueryProvider())
        # The THREE SURFACES (listener_enabled / census_enabled /
        # query_api) plus the DEGRADED registry flag (H5).  The flag is
        # an ADDITIVE envelope member: the frozen contract's §4 rule
        # says an additive change (a new optional field) does NOT bump
        # ``schema_version`` and consumers must ignore unknown members.
        assert set(activation) == {
            "listener_enabled",
            "census_enabled",
            "query_api",
            "registry_read_ok",
        }
        assert activation["query_api"] == "auth-gated-always-on"
        assert activation["registry_read_ok"] is True

    def test_the_families_envelope_carries_the_families(self) -> None:
        from daemon.services.metrics_query.envelope import build_families_envelope

        metric = _published(_metric("ensemble_provider_envelope_gauge"))
        provider = RegistryMetricsQueryProvider(metrics_provider=lambda: [metric])
        envelope = build_families_envelope(provider)
        assert envelope["families"][0]["id"] == "ensemble_provider_envelope_gauge"
        assert envelope["truncated"] is False

    def test_the_series_envelope_reports_the_observed_interval(self) -> None:
        from daemon.services.metrics_query.envelope import build_series_envelope

        provider = RegistryMetricsQueryProvider(
            metrics_provider=lambda: [],
            sampler_interval_s=10.0,
            publisher_cadence=lambda: 15.0,
        )
        envelope = build_series_envelope(provider)
        assert envelope["window"]["interval_s"] == 15.0
        assert envelope["window"]["interval_s_source"] == "observed"
        assert envelope["stale"] is False

    def test_the_series_envelope_flags_a_stale_sampler(self) -> None:
        from daemon.services.metrics_query.envelope import build_series_envelope

        provider = RegistryMetricsQueryProvider(
            metrics_provider=lambda: [],
            sampler_interval_s=10.0,
            publisher_cadence=lambda: 15.0,
        )
        provider._ring = _StaleRing()
        envelope = build_series_envelope(provider)
        assert envelope["stale"] is True
        assert envelope["last_sample_age_s"] > 45.0


class _StaleRing:
    """A ring whose newest sample is ancient (a wedged sampler)."""

    def __init__(self) -> None:
        self.window_s = 7_200

    def newest_timestamp(self) -> int:
        return 1_000

    def oldest_timestamp(self) -> int:
        return 1_000

    def keys(self) -> list[str]:
        return []

    def points(self, key: str) -> list[tuple[int, float]]:
        return []

    def histogram_points(self, key: str) -> list[dict]:
        return []


class TestCursor:
    """AM-6 — the cursor is opaque and its failure is an expected 400."""

    def test_a_cursor_round_trips(self) -> None:
        from daemon.services.metrics_query.envelope import decode_cursor, encode_cursor

        payload = {"t": 1_700_000_000, "o": 12}
        cursor = encode_cursor(payload)
        assert decode_cursor(cursor) == payload

    def test_an_undecodable_cursor_raises_the_typed_error(self) -> None:
        from daemon.services.metrics_query.envelope import InvalidCursor, decode_cursor

        with pytest.raises(InvalidCursor):
            decode_cursor("not-a-cursor!!")

    def test_an_empty_cursor_raises_the_typed_error(self) -> None:
        from daemon.services.metrics_query.envelope import InvalidCursor, decode_cursor

        with pytest.raises(InvalidCursor):
            decode_cursor("")

    def test_a_cursor_without_an_instant_raises_the_typed_error(self) -> None:
        from daemon.services.metrics_query.envelope import (
            InvalidCursor,
            cursor_is_expired,
        )

        with pytest.raises(InvalidCursor):
            cursor_is_expired({"o": 1}, oldest_timestamp=1_000, now=2_000)

    def test_a_cursor_older_than_the_buffer_expires(self) -> None:
        from daemon.services.metrics_query.envelope import cursor_is_expired

        assert (
            cursor_is_expired({"t": 500}, oldest_timestamp=1_000, now=2_000) is True
        )
        assert (
            cursor_is_expired({"t": 1_500}, oldest_timestamp=1_000, now=2_000) is False
        )


class TestSelectors:
    """AM-1 — the label-aware selector."""

    def test_a_name_only_selector_parses(self) -> None:
        from daemon.services.metrics_query.selectors import parse_series_selector

        assert parse_series_selector("ensemble_x") == ("ensemble_x", {})

    def test_a_label_aware_selector_parses(self) -> None:
        from daemon.services.metrics_query.selectors import parse_series_selector

        name, labels = parse_series_selector("ensemble_x{a=1,b=2}")
        assert name == "ensemble_x"
        assert labels == {"a": "1", "b": "2"}

    def test_a_malformed_selector_raises_the_typed_error(self) -> None:
        from daemon.services.metrics_query.selectors import (
            InvalidLabelFilter,
            parse_series_selector,
        )

        for bad in ("", "ensemble_x{a=1", "ensemble_x{a}"):
            with pytest.raises(InvalidLabelFilter):
                parse_series_selector(bad)

    def test_a_name_only_selector_matches_every_variant(self) -> None:
        from daemon.services.metrics_query.selectors import selector_matches

        assert selector_matches("ensemble_x", {"a": "1"}, ("ensemble_x", {})) is True

    def test_a_label_aware_selector_matches_one_variant(self) -> None:
        from daemon.services.metrics_query.selectors import selector_matches

        assert (
            selector_matches("ensemble_x", {"a": "1"}, ("ensemble_x", {"a": "1"}))
            is True
        )
        assert (
            selector_matches("ensemble_x", {"a": "2"}, ("ensemble_x", {"a": "1"}))
            is False
        )


class TestDownsample:
    """AM-5 — the downsample is bucket-max, never every-kth."""

    def test_a_window_that_fits_is_returned_unchanged(self) -> None:
        from daemon.services.metrics_query.downsample import bucket_max

        points = [(1, 1.0), (2, 2.0)]
        assert bucket_max(points, 240) == points

    def test_the_bucket_maximum_is_kept(self) -> None:
        from daemon.services.metrics_query.downsample import bucket_max

        points = [(1, 1.0), (2, 9.0), (3, 2.0), (4, 3.0)]
        assert bucket_max(points, 2) == [(2, 9.0), (4, 3.0)]

    def test_the_downsample_never_exceeds_the_limit(self) -> None:
        from daemon.services.metrics_query.downsample import bucket_max

        points = [(index, float(index)) for index in range(1_000)]
        assert len(bucket_max(points, 240)) <= 240

    def test_the_window_slice_drops_the_outdated_samples(self) -> None:
        from daemon.services.metrics_query.downsample import slice_window

        points = [(1_000, 1.0), (1_100, 2.0), (2_000, 3.0)]
        assert slice_window(points, now=2_000, window_s=100) == [(2_000, 3.0)]


class TestChartPrimaryRoster:
    """Plan task 2.7b — the chart-primary roster."""

    def test_the_roster_carries_thirteen_families(self) -> None:
        """The roster count pin (12 → 13 with F23, the nest-anomaly lane).

        The method name carries the CURRENT count so a future bump that
        touches only the literal reads as a stale name (the honesty
        pass the privileged-set pins follow).
        """
        from daemon.services.metrics_query.roster import roster_family_names

        assert len(roster_family_names()) == 13

    def test_the_dropped_family_is_absent(self) -> None:
        from daemon.services.metrics_query.roster import roster_family_names

        assert "F2" not in roster_family_names()

    def test_every_role_is_a_declared_mode(self) -> None:
        from daemon.checkpoint_metrics import CHART_ROLES
        from daemon.services.metrics_query.roster import CHART_PRIMARY_ROSTER

        for entry in CHART_PRIMARY_ROSTER:
            for series in entry.series:
                assert series.chart_role in CHART_ROLES

    def test_the_counters_render_step(self) -> None:
        from daemon.services.metrics_query.roster import (
            CHART_ROLE_STEP,
            roster_role_for,
        )

        assert (
            roster_role_for("F10", "ensemble_job_queue_items_created_total")
            == CHART_ROLE_STEP
        )
        assert roster_role_for("F10", "ensemble_job_queue_depth") == "line"

    def test_the_roster_is_well_formed(self) -> None:
        from daemon.services.metrics_query.roster import assert_roster_is_well_formed

        assert_roster_is_well_formed()

    def test_a_roster_role_can_be_looked_up_per_family(self) -> None:
        from daemon.services.metrics_query.roster import roster_role_for

        assert roster_role_for("F1", "ensemble_loop_lag_seconds") == "line"
        assert roster_role_for("F99", "ensemble_nope") is None
