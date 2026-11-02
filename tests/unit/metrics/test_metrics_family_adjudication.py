"""The ADJUDICATION PACK — the plan's task 4.10 (one per cluster).

The registered pack ids of this file:

* ``metrics_family_adjudication_w1`` — the W1 verdict set;
* ``metrics_family_adjudication_w2`` — the W2 verdict set;
* ``metrics_family_adjudication_w3`` — the W3 verdict set.

WHAT THE PACK PROVES
--------------------
* every catalog family F0.1–F22 carries exactly ONE verdict with a
  seam anchor;
* every OUT metric is absent from the registry **AND** from the
  ``/metrics`` body — including F2 and the F21 phase families;
* every registered family declares ``allowed_values`` for every label
  key (AM-2 — the P4-M9 mutation) and carries its family metadata
  (AM-3 — the P4-M10 mutation);
* no producer module grew a new observe/inc call (AD3 — the P4-M2
  mutation): the lane's daemon-side diff is confined to the wiring
  package, the sampler's default and the F7 bytes call site;
* the DB-pull discipline (AM-11 — the P4-M11 mutation): a ONE-SHOT
  connection per tick, closed inside the tick, never a pool;
* the WS-A ancestry verdict honours the rc semantics (AM-24 — the
  P4-M13 mutation): rc=1 is a DEFER, never a PASS;
* the measured label space stays inside the declared bands and the
  cumulative count stays inside the pinned budget.
"""

from __future__ import annotations

import ast
import subprocess
import warnings
from pathlib import Path
from typing import Any

import pytest

from daemon import checkpoint_metrics as cm
from daemon.services.metrics_query.wiring import adjudication
from daemon.services.metrics_query.wiring import registry
from daemon.services.metrics_query.wiring import sources as wiring_sources
from daemon.services.metrics_query.wiring import vocab
from daemon.services.metrics_query.wiring import ws_a

#: The P4 STAGE's base commit — the diff-review pin's anchor.  It is
#: the tree the P4 stage started from (the P3 stage's tip: the query
#: door + the lifespan wiring).  The pin is about THIS stage's diff —
#: the P3 stage legitimately touched daemon/api.py.
LANE_BASE = "644c2bfaaa48514ec7de79f7f4bcd078a700cdbe"

#: The daemon-side files the lane MAY touch.
#:
#: THE PIN PROTECTS THE PRODUCERS, NOT THE LANE'S OWN SURFACES.  The
#: original allowlist was written for the P4 stage (the wiring package
#: plus the F7 bytes call site).  The fix cycle legitimately touches the
#: lane's OWN surfaces — the query package (the ring, the provider, the
#: envelope, the constants) and the query router (the door) — and those
#: are NOT producer modules: no producer may grow an observe/inc call,
#: and every producer path stays outside this list.
ALLOWED_DAEMON_DIFF = (
    "daemon/persistence.py",
    "daemon/routers/metrics.py",
    "daemon/services/metrics_query/constants.py",
    "daemon/services/metrics_query/envelope.py",
    "daemon/services/metrics_query/provider.py",
    "daemon/services/metrics_query/ring.py",
    "daemon/services/metrics_query/sampler.py",
)

#: The wiring package's root (the new module).
WIRING_ROOT = "daemon/services/metrics_query/wiring"


def _closed_label_space(metrics: list[cm.Metric]) -> int:
    """The closed label-space series count of a family's metrics.

    A gauge/counter contributes one series per label combination; a
    histogram contributes ``(buckets + 2)`` per combination (the ``le``
    variants plus the count and the sum) — the catalog's own counting
    convention.
    """
    total = 0
    for metric in metrics:
        combinations = 1
        for key in metric.label_keys:
            allowed = metric.allowed_values.get(key)
            combinations *= len(allowed) if allowed is not None else 1
        if metric.kind == "histogram":
            combinations *= len(metric.buckets or ()) + 2
        total += combinations
    return total


class TestTheAdjudicationTable:
    def test_every_catalog_family_carries_exactly_one_verdict(self) -> None:
        verdicts = adjudication.CATALOG_FAMILY_VERDICTS
        assert len(verdicts) == 26
        expected = {"F0.1", "F0.2", "F0.3", "F0.4"} | {f"F{n}" for n in range(1, 23)}
        assert set(verdicts) == expected
        for family, (verdict, seam) in verdicts.items():
            assert verdict in adjudication.VALID_VERDICTS, family
            assert seam and seam.strip(), family

    def test_the_binding_subset_is_sixteen_of_twenty_six(self) -> None:
        verdicts = adjudication.CATALOG_FAMILY_VERDICTS
        full = [f for f, (v, _s) in verdicts.items() if v == "IN"]
        partial = [f for f, (v, _s) in verdicts.items() if v == "PARTIAL"]
        assert len(full) == 10
        assert len(partial) == 6
        assert tuple(sorted(full + partial)) == tuple(sorted(adjudication.REGISTERED_FAMILIES))

    def test_the_registered_families_match_the_verdicts(self) -> None:
        registered = set(registry.registered_families())
        assert registered == set(adjudication.REGISTERED_FAMILIES)


class TestOutMetricAbsence:
    def test_no_out_metric_is_registered(self) -> None:
        names = {m.name for m in cm.registered_metrics()}
        for out_name in adjudication.OUT_METRIC_NAMES:
            assert out_name not in names, out_name

    def test_no_out_metric_is_in_the_metrics_body(self) -> None:
        body = cm.render_metrics()
        for out_name in adjudication.OUT_METRIC_NAMES:
            assert out_name not in body, out_name

    def test_f2_is_out_and_absent(self) -> None:
        """P4-M12 — the value ruling's drop inverted must go RED."""
        verdict, _seam = adjudication.CATALOG_FAMILY_VERDICTS["F2"]
        assert verdict == "OUT"
        assert "F2" not in registry.registered_families()

    def test_the_f21_phase_families_are_absent(self) -> None:
        """P4-M7 — the conditionality verdict (AD4(b) / D-4)."""
        names = {m.name for m in cm.registered_metrics()}
        assert "ensemble_upgrade_phase_seconds" not in names
        assert "ensemble_upgrade_phase_total" not in names


class TestRegistrationStructure:
    def test_every_registered_metric_declares_its_label_values(self) -> None:
        """AM-2 — a key without allowed_values is char-checked only."""
        for metric in registry.registered_family_metrics():
            for key in metric.label_keys:
                assert key in metric.allowed_values, (metric.name, key)
                assert metric.allowed_values[key], (metric.name, key)

    def test_every_registered_metric_carries_its_family_metadata(self) -> None:
        """AM-3 — the door describes every family it serves."""
        for metric in registry.registered_family_metrics():
            assert metric.family in adjudication.REGISTERED_FAMILIES
            assert metric.source_idiom in ("registry", "postgres_census", "wedge_capture")
            assert metric.durability in ("process", "durable")
            assert isinstance(metric.cardinality_weight, int)

    def test_a_label_key_without_allowed_values_is_refused(self) -> None:
        """P4-M9 — the mutation drops the allowed_values declaration."""
        with pytest.raises(ValueError, match="AM-2"):
            registry.register_family_metric(
                family="F-test",
                cluster="W-test",
                name="ensemble_test_no_allowed_values",
                kind="gauge",
                verdict="IN",
                seam="a test seam",
                label_keys=("thing",),
                allowed_values=None,
                help="",
                source_idiom="registry",
                durability="process",
                cardinality_weight=1,
            )

    def test_a_registration_without_its_metadata_is_refused(self) -> None:
        """P4-M10 — the mutation drops the family metadata kwargs."""
        with pytest.raises(TypeError):
            registry.register_family_metric(  # type: ignore[call-arg]
                family="F-test",
                cluster="W-test",
                name="ensemble_test_no_metadata",
                kind="gauge",
                verdict="IN",
                seam="a test seam",
                help="",
                source_idiom="registry",
                durability="process",
            )


class TestNoProducerInstrumentation:
    def test_the_daemon_diff_is_confined_to_the_allowed_files(self) -> None:
        """P4-M2 — a new increment at a producer site must go RED.

        The probe compares the lane's BASE to the WORKING TREE (the
        two-dot form) — an uncommitted producer-site edit is exactly
        the defect class this pin exists for.
        """
        completed = subprocess.run(
            ["git", "diff", "--name-only", LANE_BASE],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        changed = [line for line in completed.stdout.splitlines() if line]
        daemon_changes = [
            path for path in changed if path.startswith("daemon/") and not path.startswith(WIRING_ROOT)
        ]
        for path in daemon_changes:
            assert path in ALLOWED_DAEMON_DIFF, path

    def test_the_wiring_package_never_touches_a_foreign_metric(self) -> None:
        """P4-M2 — an increment on somebody else's counter must go RED."""
        own_names = {m.name for m in registry.registered_family_metrics()}
        root = Path(__file__).resolve().parents[3] / "daemon" / "services" / "metrics_query" / "wiring"
        offenders: list[str] = []
        for path in sorted(root.glob("*.py")):
            tree = ast.parse(path.read_text())
            # The metric OBJECTS this module registered.
            local = {
                node.targets[0].id
                for node in tree.body
                if isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute):
                    continue
                if func.attr not in ("inc", "observe", "set"):
                    continue
                receiver = func.value
                if isinstance(receiver, ast.Name) and receiver.id not in local:
                    offenders.append(f"{path.name}:{node.lineno}:{receiver.id}")
        assert offenders == []


class TestDbPullDiscipline:
    def test_the_wiring_package_never_imports_a_pool(self) -> None:
        """AM-11 — the query package must not borrow a pooled session."""
        root = Path(__file__).resolve().parents[3] / "daemon" / "services" / "metrics_query" / "wiring"
        for path in sorted(root.glob("*.py")):
            source = path.read_text()
            for forbidden in ("from sqlalchemy", "import sqlalchemy", "AsyncConnectionPool", "ConnectionPool"):
                assert forbidden not in source, (path.name, forbidden)

    def test_a_failing_query_is_logged_at_warning_rate_limited(self, caplog) -> None:
        """A-3 — a persistently failing family query must be VISIBLE.

        The failure used to be swallowed at DEBUG: a family whose
        database is gone was indistinguishable, in the log, from a
        family that simply had no observations.  The failure is now a
        WARNING — but RATE LIMITED, so a broken family cannot flood the
        log at the sampler's cadence.
        """
        import logging

        from daemon.services.metrics_query.wiring import db

        class _BadConn:
            def cursor(self) -> Any:
                raise RuntimeError("the probe connection is broken")

        statement = "SELECT the_a3_rate_limited_probe"
        with caplog.at_level(
            logging.WARNING, logger="daemon.services.metrics_query.wiring.db"
        ):
            assert db.fetch_rows(_BadConn(), statement) == []
            first = [
                record
                for record in caplog.records
                if "DB query failed" in record.getMessage()
            ]
            assert len(first) == 1, "the first failure must be logged at WARNING"
            assert db.fetch_rows(_BadConn(), statement) == []
            second = [
                record
                for record in caplog.records
                if "DB query failed" in record.getMessage()
            ]
            assert len(second) == 1, (
                "the second failure inside the window must be rate limited"
            )

    def test_a_db_adapter_opens_and_closes_one_connection_per_tick(self) -> None:
        """AM-11 — a persistent per-adapter connection must go RED."""
        from daemon.services.metrics_query.wiring import w2

        opened: list[str] = []
        closed: list[str] = []

        class _Conn:
            def __init__(self) -> None:
                self.closed = False

            def cursor(self) -> Any:
                class _C:
                    def __enter__(self) -> Any:
                        return self

                    def __exit__(self, *exc: Any) -> bool:
                        return False

                    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
                        return None

                    def fetchall(self) -> list[Any]:
                        return [("queued", 1)]

                return _C()

            def close(self) -> None:
                self.closed = True
                closed.append("closed")

        def _connect(dsn: str, **kwargs: Any) -> Any:
            opened.append(dsn)
            return _Conn()

        wiring_sources.bind_sources(
            wiring_sources.WiringSources(dsn="postgresql://fake", db_connect=_connect)
        )
        w2.adapt_f10(None)
        assert len(opened) == 1
        assert len(closed) == 1

    def test_a_second_tick_opens_a_fresh_connection(self) -> None:
        from daemon.services.metrics_query.wiring import w2

        opened: list[str] = []

        class _Conn:
            def cursor(self) -> Any:
                class _C:
                    def __enter__(self) -> Any:
                        return self

                    def __exit__(self, *exc: Any) -> bool:
                        return False

                    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
                        return None

                    def fetchall(self) -> list[Any]:
                        return [("queued", 1)]

                return _C()

            def close(self) -> None:
                return None

        def _connect(dsn: str, **kwargs: Any) -> Any:
            opened.append(dsn)
            return _Conn()

        wiring_sources.bind_sources(
            wiring_sources.WiringSources(dsn="postgresql://fake", db_connect=_connect)
        )
        w2.adapt_f10(None)
        w2.adapt_f10(None)
        assert len(opened) == 2


class TestWsAAncestryVerdict:
    def test_rc_zero_is_a_pass(self) -> None:
        verdict, rc = ws_a.ws_a_ancestry_verdict(runner=lambda argv: 0)
        assert verdict == "PASS"
        assert rc == 0

    def test_rc_one_is_a_defer_not_a_pass(self) -> None:
        """P4-M13 — the ancestry check inverted must go RED."""
        verdict, rc = ws_a.ws_a_ancestry_verdict(runner=lambda argv: 1)
        assert verdict == "DEFER"
        assert rc == 1

    def test_an_unexpected_rc_stops_the_lane(self) -> None:
        verdict, rc = ws_a.ws_a_ancestry_verdict(runner=lambda argv: 128)
        assert verdict == "STOP"
        assert rc == 128

    def test_the_live_verdict_is_recorded(self) -> None:
        verdict, rc = ws_a.ws_a_ancestry_verdict(repo="HEAD")
        assert verdict in ("PASS", "DEFER", "STOP")
        assert rc in (0, 1) or verdict == "STOP"


class TestTheBudget:
    def test_the_vocabulary_version_is_not_double_bumped(self) -> None:
        """THE FINDING — P4 requires NO further vocabulary bump.

        The P1 contract freezes ``vocabulary_version: 2`` ("2 after this
        lane") and P2's success criterion is ``VOCABULARY_VERSION == 2``
        — Stage A delivered that bump (the F7 op narrowing).  P4's new
        families' label vocabularies are part of the SAME frozen annex
        (the D5 F18 row puts the amended audit vocabulary on the 1→2
        bump), so the registry-side version stays 2.  A bump to 3 here
        would break the frozen contract's pinned value.
        """
        assert cm.VOCABULARY_VERSION == 2

    def test_each_family_stays_inside_its_declared_band(self) -> None:
        specs = registry.registered_families()
        metrics = list(registry.registered_family_metrics())
        for family, band in adjudication.FAMILY_SERIES_BANDS.items():
            family_metrics = [m for m in metrics if m.family == family]
            assert family_metrics, family
            assert family in specs, family
            measured = _closed_label_space(family_metrics)
            assert measured <= band, (family, measured, band)

    def test_the_cumulative_count_stays_inside_the_global_cap(self) -> None:
        """The ONE enforced series bound (A-6) — the GLOBAL cap.

        THE CONVENTION IS EXPLICIT, because the plan's projection is a
        DIFFERENT convention: the plan's 363 is the catalog's LIVE
        ESTIMATE (only the combinations that fire), while this pack
        MEASURES the CLOSED label space (every declared combination).
        The ceiling convention OVER-COUNTS the dynamic vocabularies, so
        the measured count is NOT comparable with the projection —
        family by family or in total — and no band assertion may
        straddle them (it would be a false red by construction).

        So the enforced bound is the convention-INSENSITIVE one: the
        measured closed-label-space count plus the registered baseline,
        against the 2000 planning cap.
        """
        metrics = list(registry.registered_family_metrics())
        measured = _closed_label_space(metrics)
        total = adjudication.BASELINE_SERIES + measured
        assert total <= adjudication.GLOBAL_SERIES_CAP, (
            f"the measured closed-label-space total {total} "
            f"({adjudication.BASELINE_SERIES} baseline + {measured} measured) "
            f"is over the {adjudication.GLOBAL_SERIES_CAP} global cap"
        )


class TestThePlanningProjectionIsNeverEnforced:
    r"""A-6 — the plan's projection is a NUMBER, never a bound.

    The constant used to be named ``BUDGET_SERIES``: the NAME read like
    an enforced budget, and the constant had ZERO consumers.  The
    leader's ruling renames it to a planning-time projection and states
    that it is NEVER machine-enforced.

    THE PIN IS THE STRUCTURE, not the number: the projection must have
    NO consumer anywhere — not in the daemon, not in a pack.  That is
    what makes the two counting conventions safe: a future change that
    compared a MEASURED count against the plan's LIVE-ESTIMATE
    projection would be a false red by construction (the ceiling
    convention over-counts the dynamic vocabularies), and this pin
    makes that change impossible to merge silently.

    It is NOT a vacuous pin: adding a consumer (in ``daemon/`` or in a
    pack) turns it RED.
    """

    _PROJECTION = "CATALOG_LIVE_ESTIMATE_SERIES_PROJECTION"

    def test_the_projection_has_no_machine_enforced_consumer(self) -> None:
        root = Path(__file__).resolve().parents[3]
        hits: list[str] = []
        for base in ("daemon", "tests"):
            for path in sorted((root / base).rglob("*.py")):
                text = path.read_text(encoding="utf-8")
                # The text pre-filter keeps the scan cheap (the whole
                # tree is read, but only the files that MENTION the
                # name are parsed) — the AST pass is what decides
                # whether a mention is a real reference.
                if self._PROJECTION not in text:
                    continue
                with warnings.catch_warnings():
                    # An unrelated file's docstring escape warning is
                    # not this pin's business (the repo's own test run
                    # surfaces those when the module is imported).
                    warnings.simplefilter("ignore", SyntaxWarning)
                    tree = ast.parse(text)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Name) and node.id == self._PROJECTION:
                        hits.append(f"{path.relative_to(root)}:{node.lineno}")
                    elif (
                        isinstance(node, ast.Attribute)
                        and node.attr == self._PROJECTION
                    ):
                        hits.append(f"{path.relative_to(root)}:{node.lineno}")
                    elif isinstance(node, ast.alias) and (
                        node.name == self._PROJECTION
                        or node.asname == self._PROJECTION
                    ):
                        hits.append(f"{path.relative_to(root)}:{node.lineno}")
        # EXACTLY ONE reference: the constant's own definition.  A
        # consumer anywhere (the daemon or a pack) adds a second hit and
        # turns this RED — that is the whole point of the pin.
        assert len(hits) == 1, hits
        assert hits[0].startswith(
            "daemon/services/metrics_query/wiring/adjudication.py:"
        ), hits
        # The rename is COMPLETE — the old name is gone, the new one is
        # exported, and the figure is the plan's.  The constant is held
        # BY NAME (a string + getattr) on purpose: a static reference
        # here would register this pin as a consumer of its own
        # subject, and the scan would stop meaning anything.
        assert "BUDGET_SERIES" not in adjudication.__all__
        assert self._PROJECTION in adjudication.__all__
        assert getattr(adjudication, self._PROJECTION) == 363

    def test_the_f21_release_vocabulary_is_bounded(self) -> None:
        """The release-version label is bounded by the retention set."""
        assert len(vocab.RELEASE_VERSIONS) <= 32
        assert vocab.RESERVED in vocab.RELEASE_VERSIONS
