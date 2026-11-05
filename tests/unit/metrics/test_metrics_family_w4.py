"""The F23 family pack (W4 — the nest-anomaly family).

ONE path everywhere (the council's W-4 ruling): this file's name
matches the module it tests (``wiring/w4.py``), and phase 6's gate
table + A7 name this exact path.

WHAT IT PROVES
--------------
* F23 is registered with its **FIVE** metrics (three pull-fed + the
  AM-13f board-suppression write-time counter + C3's
  capacity-exhausted write-time counter);
* every DECLARED label key carries ``allowed_values`` (AM-2 — and a
  registration that omits one RAISES, the AM-2 hard-raise);
* the adapter early-returns when ``dsn is None``;
* a failing query is a rate-limited WARNING and the tick survives;
* the closed label space equals the declared band (**67**);
* **F23 PUBLISHES a real series** — a seeded scratch-PostgreSQL row
  ⇒ a live series on the provider (not merely a registration; a
  silent-dark F23 must not ship looking green);
* ALL FIVE F23 charted series' roster roles are asserted (AM-2c) +
  the general roster-coverage pin ("every registered metric of a
  roster family has a roster entry");
* **AM-2d** — the adapter contains NO pre-fold (the RAW stored token
  reaches ``.set()``); an out-of-vocabulary stored value increments
  ``ensemble_metrics_invalid_label_total`` while the published series
  still shows ``other``;
* the AM-2(e)/NR-11 monotonic disclosure — the two stored-row
  counters carry ``monotonic: true`` although retention can lower
  the value (the F3 house precedent; the general fix is routed to
  the central-metrics lane, not this one).

THE SCRATCH-POSTGRESQL LEG builds its OWN uniquely-named scratch
database over TCP (the house recipe — the coordinates come from the
``PG_TEST_*`` env vars ONLY; ``POSTGRES_*`` / ``DATABASE_URL`` are
NEVER read) and DROPS it at teardown.  It never touches a live
database: the name is random and the leg skips unless the host is
loopback.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from daemon import checkpoint_metrics as cm
from daemon.services.metrics_query.roster import (
    CHART_PRIMARY_ROSTER,
    CHART_ROLE_LINE,
    CHART_ROLE_STEP,
    roster_family_names,
    roster_role_for,
)
from daemon.services.metrics_query.wiring import adjudication, vocab, w4
from daemon.services.metrics_query.wiring import sources as wiring_sources
from daemon.services.metrics_query.wiring.registry import registered_family_metrics

REPO_ROOT = Path(__file__).resolve().parents[3]

#: The five F23 metric names (the registration order).
F23_NAMES = (
    "ensemble_anomaly_reports_total",
    "ensemble_anomaly_clusters_open",
    "ensemble_anomaly_reports_rate_limited_total",
    "ensemble_anomaly_board_items_suppressed_total",
    "ensemble_anomaly_capacity_exhausted_total",
)


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    cm.reset_for_tests()
    yield
    cm.reset_for_tests()
    wiring_sources.unbind_sources()


# ── a fake one-shot connection (the house family-pack pattern) ────────────


class _FakeCursor:
    def __init__(
        self,
        rows_by_sql: dict[str, list[tuple[Any, ...]]],
        executed: list[tuple[str, tuple[Any, ...]]] | None = None,
    ) -> None:
        self._rows_by_sql = rows_by_sql
        self._executed = executed

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self._executed is not None:
            self._executed.append((sql, params))
        self._sql = sql
        self._rows = self._rows_by_sql.get(sql, [])

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _FakeConn:
    def __init__(self, rows_by_sql: dict[str, list[tuple[Any, ...]]]) -> None:
        self._rows_by_sql = rows_by_sql
        self.closed = False
        #: every (sql, params) pair executed on this connection — the
        #: ADVISORY-⑦ pin reads the BOUND cutoff parameter from here.
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._rows_by_sql, self.executed)

    def close(self) -> None:
        self.closed = True


def _bind_fake(rows_by_sql: dict[str, list[tuple[Any, ...]]]) -> _FakeConn:
    conn = _FakeConn(rows_by_sql)

    def _connect(dsn: str, **kwargs: Any) -> _FakeConn:
        return conn

    wiring_sources.bind_sources(
        wiring_sources.WiringSources(dsn="postgresql://fake", db_connect=_connect)
    )
    return conn


class TestTheRegistration:
    def test_f23_registers_five_metrics(self) -> None:
        metrics = [m for m in registered_family_metrics() if m.family == "F23"]
        assert tuple(m.name for m in metrics) == F23_NAMES
        for metric in metrics:
            assert metric.cluster if hasattr(metric, "cluster") else True

    def test_the_family_is_wired_into_the_sampler(self) -> None:
        """M-3b — the family is not merely REGISTERED: its pull adapter
        is WIRED (the sampler's stage A runs it).

        A family that registers its metrics but never wires its adapter
        renders NO series — the F1/F3/F6 "dark family" trap.  This pin
        is the structural half of the publish assertion below.
        """
        from daemon.services.metrics_query.wiring.registry import (
            registered_pull_adapter_families,
        )

        assert "F23" in registered_pull_adapter_families()

    def test_the_family_carries_the_cluster_and_verdict(self) -> None:
        from daemon.services.metrics_query.wiring.registry import registered_families

        specs = registered_families()
        assert "F23" in specs
        spec = specs["F23"]
        assert spec.cluster == "W4"
        assert spec.verdict == "IN"
        assert spec.metrics == F23_NAMES

    def test_every_declared_label_key_has_allowed_values(self) -> None:
        """AM-2 — the two labelled metrics carry closed sets; the two
        unlabelled write-time counters declare NO label keys and NO
        allowed_values entries (an unlabelled counter is legal)."""
        metrics = {m.name: m for m in registered_family_metrics() if m.family == "F23"}
        labelled = metrics["ensemble_anomaly_reports_total"]
        assert tuple(labelled.label_keys) == ("category",)
        assert labelled.allowed_values["category"] == frozenset(vocab.ANOMALY_CATEGORIES)
        open_gauge = metrics["ensemble_anomaly_clusters_open"]
        assert tuple(open_gauge.label_keys) == ("category", "severity")
        assert open_gauge.allowed_values["category"] == frozenset(
            vocab.ANOMALY_CATEGORIES
        )
        assert open_gauge.allowed_values["severity"] == frozenset(
            vocab.ANOMALY_SEVERITIES
        )
        for unlabelled in (
            "ensemble_anomaly_board_items_suppressed_total",
            "ensemble_anomaly_capacity_exhausted_total",
        ):
            metric = metrics[unlabelled]
            assert tuple(metric.label_keys) == ()
            assert not metric.allowed_values

    def test_a_label_key_without_allowed_values_raises(self) -> None:
        """AM-2's hard-raise — the M-3a mutation shape (widening
        ``allowed_values`` to free text, or dropping the set) cannot
        register at all."""
        from daemon.services.metrics_query.wiring.registry import (
            register_family_metric,
        )

        with pytest.raises(ValueError, match="AM-2"):
            register_family_metric(
                family="F23-TEST",
                cluster="W4",
                name="ensemble_anomaly_test_unfenced",
                kind="counter",
                verdict="IN",
                seam="test",
                label_keys=("category",),
                allowed_values=None,
                source_idiom="registry",
                durability="durable",
                cardinality_weight=1,
            )

    def test_the_vocabulary_is_phase_1s(self) -> None:
        assert "other" in vocab.ANOMALY_CATEGORIES  # the fold target is a MEMBER
        assert len(vocab.ANOMALY_CATEGORIES) == 13
        assert len(vocab.ANOMALY_SEVERITIES) == 3

    def test_the_durabilities_are_per_member(self) -> None:
        metrics = {m.name: m for m in registered_family_metrics() if m.family == "F23"}
        assert metrics["ensemble_anomaly_reports_total"].durability == "durable"
        assert metrics["ensemble_anomaly_clusters_open"].durability == "durable"
        assert (
            metrics["ensemble_anomaly_reports_rate_limited_total"].durability
            == "durable"
        )
        # The TWO write-time counters are PROCESS-durable (in-RAM,
        # reset on restart — honest for refusal/suppression tallies).
        assert (
            metrics["ensemble_anomaly_board_items_suppressed_total"].durability
            == "process"
        )
        assert (
            metrics["ensemble_anomaly_capacity_exhausted_total"].durability
            == "process"
        )

    def test_the_monotonic_disclosure_nr11(self) -> None:
        """AM-2(e)/NR-11 — the two stored-row counters derive
        ``monotonic: true`` from ``kind=counter`` although the VALUE can
        decrease when retention prunes.  The disclosure is recorded
        here; the general fix is routed to the central-metrics lane."""
        metrics = {m.name: m for m in registered_family_metrics() if m.family == "F23"}
        for name in (
            "ensemble_anomaly_reports_total",
            "ensemble_anomaly_reports_rate_limited_total",
        ):
            metric = metrics[name]
            assert metric.kind == "counter"
            assert "stored-row count" in metric.help, name
        # The open gauge is a gauge — monotonicity is not claimed.
        assert metrics["ensemble_anomaly_clusters_open"].kind == "gauge"

    def test_the_write_time_counters_increment_from_the_service(self) -> None:
        """C4's wiring obligation — the SERVICE's increment points hit
        the REAL handles (one event stream, one store; no parallel
        local int remains in the service)."""
        from daemon.services.anomaly_service import (
            _write_time_inc,
            _write_time_read,
        )

        before_cap = _write_time_read("capacity_exhausted")
        before_sup = _write_time_read("board_items_suppressed")
        _write_time_inc("capacity_exhausted")
        _write_time_inc("board_items_suppressed")
        assert _write_time_read("capacity_exhausted") == before_cap + 1
        assert _write_time_read("board_items_suppressed") == before_sup + 1
        # And there is NO parallel counter attribute on the service
        # (only the per-instance READ BASELINE, never a second store).
        import inspect

        from daemon.services import anomaly_service

        source = inspect.getsource(anomaly_service)
        assert "_capacity_exhausted_total" not in source
        assert "_board_items_suppressed_total" not in source


class TestTheAdapter:
    def test_no_dsn_renders_no_series_and_never_raises(self) -> None:
        wiring_sources.bind_sources(wiring_sources.WiringSources())
        w4.adapt_f23(None)  # the early return — no connection, no series
        assert w4.anomaly_reports_total.get() == 0

    def test_the_connection_is_closed_inside_the_tick(self) -> None:
        conn = _bind_fake({})
        w4.adapt_f23(None)
        assert conn.closed is True

    def test_the_aggregates_set_the_raw_tokens(self) -> None:
        conn = _bind_fake(
            {
                w4._ANOMALY_REPORTS_ACCEPTED_SQL: [("tool-failure", 3)],
                w4._ANOMALY_REPORTS_RATE_LIMITED_SQL: [("llm-provider", 2)],
                w4._ANOMALY_CLUSTERS_OPEN_SQL: [("performance", "critical", 1)],
            }
        )
        w4.adapt_f23(None)
        assert conn.closed
        assert w4.anomaly_reports_total.get(category="tool-failure") == 3
        assert w4.anomaly_reports_rate_limited_total.get(category="llm-provider") == 2
        assert w4.anomaly_clusters_open.get(category="performance", severity="critical") == 1

    def test_the_open_clusters_cutoff_is_bound_not_hardcoded(self) -> None:
        """ADVISORY-⑦ (code council fix cycle, 2026-11-05) — the
        open-clusters cutoff is COMPUTED IN PYTHON from
        ``NestReportConfig().resolve_window_seconds`` and BOUND as a
        query parameter (the AM-4 house idiom). The SQL carries no
        ``interval`` literal, so the resolve-window knob cannot
        silently desync from the query."""
        from datetime import datetime, timedelta, timezone

        from daemon.config import NestReportConfig

        sql = w4._ANOMALY_CLUSTERS_OPEN_SQL
        assert "interval" not in sql.lower(), (
            "the SQL must not hardcode an interval literal — the "
            "window comes from the knob, as a bound parameter"
        )
        assert "%s" in sql

        conn = _bind_fake({sql: []})
        before = datetime.now(timezone.utc)
        w4.adapt_f23(None)
        after = datetime.now(timezone.utc)
        assert conn.closed

        executed = dict(
            (sql_, params) for sql_, params in conn.executed
        ).get(sql)
        assert executed is not None, "the open-clusters query ran"
        window = NestReportConfig().resolve_window_seconds
        assert len(executed) == 1
        (cutoff,) = executed
        assert isinstance(cutoff, datetime)
        # the BOUND cutoff is now - resolve_window_seconds, i.e. derived
        # from the same knob the service's resolution sweep reads.
        assert (
            before - timedelta(seconds=window)
            <= cutoff
            <= after - timedelta(seconds=window)
        ), (
            f"cutoff {cutoff} is not [now-{window}s] — the parameter "
            "is not derived from the resolve-window knob"
        )

    def test_the_cutoff_helper_derives_from_the_knob_deterministically(
        self,
    ) -> None:
        """The helper half of ⑦: a fixed ``now`` + a fixed window land
        exactly ``now - window`` — the knob flows through the helper
        with no hidden constant."""
        from datetime import datetime, timedelta, timezone

        now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
        got = w4._open_clusters_cutoff(now=now, window_seconds=7200)
        assert got == now - timedelta(seconds=7200)
        assert w4._open_clusters_cutoff(now=now, window_seconds=1) == (
            now - timedelta(seconds=1)
        )

    def test_a_failing_query_is_a_rate_limited_warning_and_the_tick_survives(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        class _BoomCursor(_FakeCursor):
            def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
                raise RuntimeError("relation \"anomaly_reports\" does not exist")

        class _BoomConn(_FakeConn):
            def cursor(self) -> _BoomCursor:
                return _BoomCursor({})

        conn = _BoomConn({})

        def _connect(dsn: str, **kwargs: Any) -> _BoomConn:
            return conn

        wiring_sources.bind_sources(
            wiring_sources.WiringSources(dsn="postgresql://fake", db_connect=_connect)
        )
        with caplog.at_level(logging.WARNING, logger="daemon.services.metrics_query.wiring.db"):
            w4.adapt_f23(None)  # must NOT raise — the tick survives
        assert conn.closed
        warnings = [
            r for r in caplog.records if r.levelno >= logging.WARNING and "query" in r.message
        ]
        assert warnings, "a failing family query must be VISIBLE in the log"

    def test_non_integer_counts_are_skipped_not_fatal(self) -> None:
        _bind_fake(
            {
                w4._ANOMALY_REPORTS_ACCEPTED_SQL: [
                    ("tool-failure", "not-a-number"),
                    ("performance", 4),
                ],
            }
        )
        w4.adapt_f23(None)
        assert w4.anomaly_reports_total.get(category="tool-failure") == 0
        assert w4.anomaly_reports_total.get(category="performance") == 4

    def test_am_2d_the_registry_folds_the_adapter_does_not(self) -> None:
        """AM-2d — an out-of-vocabulary stored token reaches ``.set()``
        RAW; the REGISTRY folds it to ``other`` AND increments
        ``ensemble_metrics_invalid_label_total`` (the drift signal),
        while the published series shows the reserved value.

        THE M-3j MUTATION TARGET: re-introducing the adapter's
        pre-fold (``token not in vocab.ANOMALY_CATEGORIES``) makes the
        invalid-label counter UNREACHABLE and this pin RED.
        """
        invalid = cm.registered_metrics()
        invalid_metric = next(
            (m for m in invalid if m.name == "ensemble_metrics_invalid_label_total"),
            None,
        )
        assert invalid_metric is not None, "the registry's own invalid-label counter"
        before = invalid_metric.get(
            metric="ensemble_anomaly_reports_total", label="category"
        )
        _bind_fake({w4._ANOMALY_REPORTS_ACCEPTED_SQL: [("ghost-category", 5)]})
        w4.adapt_f23(None)
        # The published series folds to the reserved member...
        assert w4.anomaly_reports_total.get(category="other") == 5
        assert w4.anomaly_reports_total.get(category="ghost-category") == 0
        # ...and the DRIFT SIGNAL fired (the pre-fold would have
        # swallowed it — a pre-folded `other` is indistinguishable
        # from a genuine one).
        after = invalid_metric.get(metric="ensemble_anomaly_reports_total", label="category")
        assert after == before + 1

    def test_the_adapter_source_contains_no_pre_fold(self) -> None:
        """The structural half of the AM-2d pin — the F3 pre-fold
        shape (``if token not in vocab.ANOMALY_CATEGORIES: token =
        vocab.RESERVED``) must NOT appear in ``w4.py``."""
        source = (REPO_ROOT / "daemon/services/metrics_query/wiring/w4.py").read_text(
            encoding="utf-8"
        )
        assert "token = vocab.RESERVED" not in source
        assert "if token not in vocab" not in source
        # The intent marker (the AM-2d comment) IS present so the next
        # implementer reads the constraint at the site.
        assert "AM-2d" in source


class TestTheRoster:
    def test_f23_is_rostered(self) -> None:
        assert "F23" in roster_family_names()

    def test_all_five_series_roles_are_asserted(self) -> None:
        """AM-2c — ALL FIVE roles, not one (a typo in one series name
        silently degrades that metric to stat-primary INSIDE a chart
        family — invisible on the wire; the M-3k mutation target)."""
        assert roster_role_for("F23", "ensemble_anomaly_reports_total") == CHART_ROLE_STEP
        assert (
            roster_role_for("F23", "ensemble_anomaly_reports_rate_limited_total")
            == CHART_ROLE_STEP
        )
        assert roster_role_for("F23", "ensemble_anomaly_clusters_open") == CHART_ROLE_LINE
        assert (
            roster_role_for("F23", "ensemble_anomaly_board_items_suppressed_total")
            == CHART_ROLE_STEP
        )
        assert (
            roster_role_for("F23", "ensemble_anomaly_capacity_exhausted_total")
            == CHART_ROLE_STEP
        )

    def test_every_registered_metric_of_a_roster_family_has_a_roster_entry(
        self,
    ) -> None:
        """The general roster-coverage pin (AM-2c), in the two
        directions that hold:

        * FORWARD — every registered F23 metric must carry a roster
          entry: a typo in one series name silently degrades that
          metric to stat-primary INSIDE a chart family (invisible on
          the wire; the M-3k mutation target).
        * REVERSE — every F23 roster entry must name a REGISTERED
          metric (a dead roster row is a lie about the rendering).

        The pin is F23-SCOPED on purpose: the tree-wide form does NOT
        hold for the pre-existing families (F1 carries the deliberate
        stat-primary ``ensemble_loop_lag_samples``; F7's PARTIAL
        roster lists ``ensemble_checkpoint_slow_ops_total`` while the
        registration omits it) — those are recorded roster facts, not
        defects this lane may "fix" in its own pack.
        """
        registered = {
            (m.family, m.name) for m in registered_family_metrics() if m.family == "F23"
        }
        assert registered, "F23 must be registered"
        for family, name in registered:
            assert roster_role_for(family, name) is not None, (family, name)
        entries = [
            series
            for entry in CHART_PRIMARY_ROSTER
            if entry.family == "F23"
            for series in entry.series
        ]
        assert entries, "F23 must be rostered"
        for series in entries:
            assert ("F23", series.name) in registered, series.name


class TestTheBand:
    def test_the_closed_label_space_equals_the_declared_band(self) -> None:
        """13 + 39 + 13 + 1 + 1 = 67 — measured with the adjudication
        pack's own counting convention.  The band has ZERO HEADROOM
        (AM-2a): it equals the closed space exactly, so a vocabulary
        extension REDS this pin BY DESIGN and must re-declare the band
        in the same change."""
        metrics = [m for m in registered_family_metrics() if m.family == "F23"]

        total = 0
        for metric in metrics:
            combinations = 1
            for key in metric.label_keys:
                allowed = metric.allowed_values.get(key)
                combinations *= len(allowed) if allowed is not None else 1
            total += combinations
        band = adjudication.FAMILY_SERIES_BANDS["F23"]
        assert total == 67
        assert total <= band

    def test_the_wiring_package_never_imports_sqlalchemy(self) -> None:
        """The raw-SQL adapter is MANDATORY (the ORM/repository path is
        impossible here) — the adjudication pack pins the whole wiring
        package; this leg keeps the proof one module away for w4."""
        source = (REPO_ROOT / "daemon/services/metrics_query/wiring/w4.py").read_text(
            encoding="utf-8"
        )
        for forbidden in ("from sqlalchemy", "import sqlalchemy"):
            assert forbidden not in source


class TestThePublishLeg:
    """**F23 PUBLISHES** — a seeded row ⇒ a live series on the provider.

    A silent-dark F23 must not ship looking green (decisions.md F23's
    disclosure + phase 6's A7).  The leg builds its OWN scratch
    PostgreSQL database (the house recipe — ``PG_TEST_*`` coordinates
    only, loopback only, DROPPED at teardown), seeds the two anomaly
    tables through the MODELS, binds the DSN, runs the REAL adapter
    and reads the REAL provider.
    """

    def test_a_seeded_row_publishes_a_live_series_on_the_provider(self) -> None:
        import os
        import uuid
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import create_engine, text
        from sqlmodel import SQLModel

        from daemon.repositories.anomaly.models import AnomalyCluster, AnomalyReport
        from daemon.services.metrics_query.provider import RegistryMetricsQueryProvider

        host = os.environ.get("PG_TEST_HOST", "localhost")
        port = os.environ.get("PG_TEST_PORT", "5432")
        user = os.environ.get("PG_TEST_USER", "ensemble")
        password = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")

        if host not in {"localhost", "127.0.0.1", "::1"}:
            pytest.skip(
                f"the scratch-PG leg only runs against a loopback server "
                f"(PG_TEST_HOST={host!r} is not loopback — it may be live)"
            )

        admin_url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/postgres"
        scratch_name = f"ensemble_anomaly_f23_{uuid.uuid4().hex[:12]}"
        assert scratch_name not in {
            "ensemble_prod",
            "ensemble_dev",
            "ensemble_prod_test",
        }
        admin = create_engine(admin_url, pool_pre_ping=True, future=True)
        try:
            with admin.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as conn:
                conn.execute(text(f'CREATE DATABASE "{scratch_name}"'))
        except Exception as exc:  # noqa: BLE001 — skip-cleanly
            admin.dispose()
            pytest.skip(f"no scratch database could be created: {exc}")

        dsn = f"postgresql://{user}:{password}@{host}:{port}/{scratch_name}"
        engine = create_engine(
            f"postgresql+psycopg://{user}:{password}@{host}:{port}/{scratch_name}",
            pool_pre_ping=True,
            future=True,
        )
        try:
            SQLModel.metadata.create_all(
                engine,
                tables=[AnomalyReport.__table__, AnomalyCluster.__table__],
            )
            now = datetime.now(timezone.utc)
            fresh = now.isoformat(timespec="microseconds")
            stale = (now - timedelta(hours=2)).isoformat(timespec="microseconds")
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO anomaly_reports (id, created_at, agent_id,"
                        " instance_id, category, category_raw, severity,"
                        " description, parent_chain, parent_chain_agents,"
                        " outcome) VALUES"
                        " ('r1', :c1, 'tester', 'i-1', 'tool-failure',"
                        " 'tool failure', 'major', 'd1', '[]'::jsonb,"
                        " '[]'::jsonb, 'accepted'),"
                        " ('r2', :c1, 'tester', 'i-1', 'tool-failure',"
                        " 'tool failure', 'critical', 'd2', '[]'::jsonb,"
                        " '[]'::jsonb, 'accepted'),"
                        " ('r3', :c1, 'tester', 'i-2', 'llm-provider',"
                        " 'llm', 'minor', 'd3', '[]'::jsonb, '[]'::jsonb,"
                        " 'rate_limited')"
                    ),
                    {"c1": fresh},
                )
                conn.execute(
                    text(
                        "INSERT INTO anomaly_clusters (id, cluster_key_hash,"
                        " tree_root_instance_id, category, first_report_at,"
                        " last_report_at, report_count, max_severity,"
                        " created_at) VALUES"
                        " ('cl-open', 'k1', 'tree-1', 'tool-failure', :c1,"
                        " :c1, 2, 'critical', :c1),"
                        " ('cl-closed', 'k2', 'tree-1', 'performance', :c2,"
                        " :c2, 5, 'major', :c2)"
                    ),
                    {"c1": fresh, "c2": stale},
                )
            engine.dispose()

            wiring_sources.bind_sources(wiring_sources.WiringSources(dsn=dsn))
            w4.adapt_f23(None)

            # THE REGISTRY carries the seeded values...
            assert w4.anomaly_reports_total.get(category="tool-failure") == 2
            assert w4.anomaly_reports_rate_limited_total.get(category="llm-provider") == 1
            assert (
                w4.anomaly_clusters_open.get(
                    category="tool-failure", severity="critical"
                )
                == 1
            )
            # ...the CLOSED cluster (last_report_at 2h ago) stays OUT —
            # open is DERIVED in the query (R5).
            assert w4.anomaly_clusters_open.get(category="performance") == 0

            # ...AND THE PROVIDER publishes F23 as a LIVE family.
            provider = RegistryMetricsQueryProvider()
            families = {family["id"]: family for family in provider.families()}
            f23 = families.get("F23")
            assert f23 is not None, "F23 must appear in the provider catalog"
            assert f23["live_series_count"] >= 3, f23["live_series_count"]
            names = {entry["name"] for entry in f23["names"]}
            assert names == set(F23_NAMES)
        finally:
            engine.dispose()
            with admin.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch_name}"'))
            admin.dispose()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
