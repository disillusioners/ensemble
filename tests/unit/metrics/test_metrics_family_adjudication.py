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

# The Decision Module P1 lane's family (``decision_overlay``) is
# registered by ITS OWN module — the census's other families live
# inside the wiring package, this one does not — so the pack loads it
# here, exactly as it loads ``ws_a``/``vocab``/``sources``.  Without
# this import the family would be absent from the registry and the
# census would (correctly) report a mismatch.
from daemon.services.decision import metrics as decision_metrics  # noqa: F401

#: The P4 STAGE's base commit — the diff-review pin's anchor.  It is
#: the tree the P4 stage started from (the P3 stage's tip: the query
#: door + the lifespan wiring).  The pin is about THIS stage's diff —
#: the P3 stage legitimately touched daemon/api.py.
LANE_BASE = "644c2bfaaa48514ec7de79f7f4bcd078a700cdbe"

#: The DECISION LANE's own daemon files — BYTE-FROZEN, EXPLICIT.
#:
#: B-4 (council round 4) — this list and ``ALLOWED_DAEMON_DIFF`` are TWO
#: INDEPENDENT literals. The previous shape DERIVED the allowlist from
#: this list (``ALLOWED = (...) + tuple(sorted(LANE_OWNED))``), which
#: made ``LANE_OWNED ⊆ ALLOWED`` true BY CONSTRUCTION: the pin's
#: "no lane-caused offender" assertion could never fail, so it proved
#: NOTHING. The conclusion ("no live escape among the lane's own
#: files") was verified BY HAND by the tester; the PIN is what was
#: tautological, and it is rewritten here so that it can fail.
#:
#: The pin now asserts the direction that CAN fail: every file in this
#: list must be PRESENT in ``ALLOWED_DAEMON_DIFF`` (a lane file dropped
#: from the allowlist REDs), and no changed daemon path may be neither
#: allowlisted nor in the recorded pre-existing set (a NEW offender
#: REDs). A file added to this list without an allowlist entry REDs —
#: which is exactly the "future lane file that is not allowlisted"
#: case the ruling names.
LANE_OWNED_DAEMON = frozenset(
    {
        "daemon/config.py",
        "daemon/manager.py",
        "daemon/services/decision/__init__.py",
        "daemon/services/decision/client.py",
        "daemon/services/decision/config.py",
        "daemon/services/decision/cost_store.py",
        "daemon/services/decision/factory.py",
        "daemon/services/decision/kill.py",
        "daemon/services/decision/logging_safe.py",
        "daemon/services/decision/MUTATION_MATRIX.md",
        "daemon/services/decision/metrics.py",
        "daemon/services/decision/overlay.py",
        "daemon/services/decision/port.py",
        "daemon/services/decision/registry.py",
        "daemon/services/decision/shadow.py",
        "daemon/services/decision/types.py",
        "daemon/services/settings_service.py",
        "daemon/services/snapshot_search_service.py",
    }
)

#: The daemon-side files the lane MAY touch.
#:
#: THE PIN PROTECTS THE PRODUCERS, NOT THE LANE'S OWN SURFACES.  The
#: original allowlist was written for the P4 stage (the wiring package
#: plus the F7 bytes call site).  The fix cycle legitimately touches the
#: lane's OWN surfaces — the query package (the ring, the provider, the
#: envelope, the constants) and the query router (the door) — and those
#: are NOT producer modules: no producer may grow an observe/inc call,
#: and every producer path stays outside this list.
#:
#: B4: the Decision Module P1 lane's OWN surfaces are listed HERE,
#: EXPLICITLY and INDEPENDENTLY of ``LANE_OWNED_DAEMON`` — the two
#: literals are compared by the pin, not merged by construction.
ALLOWED_DAEMON_DIFF = (
    "daemon/persistence.py",
    "daemon/routers/metrics.py",
    # The craftsmanship fold (H2/D-items): the shutdown COMMENT in the
    # app factory (a comment — no observe/inc call) and the query
    # package's own selector + re-export surfaces (dead exports
    # removed; the package NEVER reads or writes a metric).
    "daemon/api.py",
    "daemon/services/metrics_query/__init__.py",
    "daemon/services/metrics_query/availability.py",
    "daemon/services/metrics_query/constants.py",
    "daemon/services/metrics_query/envelope.py",
    "daemon/services/metrics_query/provider.py",
    "daemon/services/metrics_query/ring.py",
    "daemon/services/metrics_query/sampler.py",
    "daemon/services/metrics_query/selectors.py",
    # ── The Decision Module P1 lane's OWN surfaces (2026-11-09) ──────
    # The lane registers its families through the WIRING registrar
    # (daemon/services/metrics_query/wiring/registry.py), so the AM-2
    # label-vocabulary enforcement and the pack's census apply to them
    # exactly as they do to every other family. Listed EXPLICITLY: this
    # list is NOT derived from LANE_OWNED_DAEMON, so the pin can fail.
    "daemon/config.py",
    "daemon/manager.py",
    "daemon/services/decision/__init__.py",
    "daemon/services/decision/client.py",
    "daemon/services/decision/config.py",
    "daemon/services/decision/cost_store.py",
    "daemon/services/decision/factory.py",
    "daemon/services/decision/kill.py",
    "daemon/services/decision/logging_safe.py",
    "daemon/services/decision/MUTATION_MATRIX.md",
    "daemon/services/decision/metrics.py",
    "daemon/services/decision/overlay.py",
    "daemon/services/decision/port.py",
    "daemon/services/decision/registry.py",
    "daemon/services/decision/shadow.py",
    "daemon/services/decision/types.py",
    "daemon/services/settings_service.py",
    "daemon/services/snapshot_search_service.py",
)

#: The allowlist entries that predate THIS lane — the metrics-query
#: lane's own surfaces (the P4 stage's list). They are FROZEN here so
#: the both-directions consistency pin can tell "the allowlist holds a
#: known prior entry" from "the allowlist silently WIDENED".
PRIOR_ALLOWLIST_ENTRIES = frozenset(
    {
        "daemon/api.py",
        "daemon/persistence.py",
        "daemon/routers/metrics.py",
        "daemon/services/metrics_query/__init__.py",
        "daemon/services/metrics_query/availability.py",
        "daemon/services/metrics_query/constants.py",
        "daemon/services/metrics_query/envelope.py",
        "daemon/services/metrics_query/provider.py",
        "daemon/services/metrics_query/ring.py",
        "daemon/services/metrics_query/sampler.py",
        "daemon/services/metrics_query/selectors.py",
    }
)


#: The daemon paths that were ALREADY offenders when this lane's base
#: (``LANE_BASE``) was cut — other lanes' committed work, recorded
#: EXPLICITLY so a NEW offender is distinguishable from the inherited
#: set. The pin names the whole set in its failure message; it never
#: silently absorbs an unknown path.
#:
#: **THE RECORD IS MAINTAINED BY THE CHANGING LANE.** A lane that
#: changes daemon files records them here in its own change, so the
#: offender stays NAMED and attributable instead of showing up as a
#: new offender in a later lane's red. The prompt-assent lane
#: (2026-11-10, Phase 1 + Phase 2) added the rows below.
PRE_EXISTING_DAEMON_OFFENDERS = frozenset(
    {
        "daemon/__init__.py",
        # prompt-assent lane (2026-11-10, Phase 1 + Phase 2): the
        # conversational approval lane's daemon surfaces.
        "daemon/repositories/prompt_assent/__init__.py",
        "daemon/repositories/prompt_assent/consume_hook.py",
        "daemon/repositories/prompt_assent/mint_context.py",
        "daemon/repositories/prompt_assent/models.py",
        "daemon/repositories/prompt_assent/repository.py",
        "daemon/repositories/prompt_version/assent_attestation.py",
        "daemon/repositories/prompt_version/repository.py",
        "daemon/repositories/factory.py",
        "daemon/migrations/versions/20261110_221500_create_prompt_assent_tokens.sql",
        "daemon/services/prompt_version_diff.py",
        "daemon/services/prompt_version_service.py",
        "daemon/sources/adapters/discord/adapter.py",
        "daemon/tools/prompt_assent.py",
        "daemon/_redact.py",
        "daemon/auth/scopes.py",
        "daemon/checkpoint_metrics.py",
        "daemon/clients/plane_http_client.py",
        "daemon/constants.py",
        "daemon/loader.py",
        "daemon/mcp/builtin_servers/base.py",
        "daemon/mcp/builtin_servers/plane.py",
        "daemon/migrations/versions/20261105_020500_create_anomaly_reports.sql",
        "daemon/migrations/versions/20261105_020501_create_anomaly_clusters.sql",
        "daemon/migrations/versions/20261105_102821_create_memoir_tables.sql",
        "daemon/migrations/versions/20261107_110001_create_project_operating_knowledge.sql",
        "daemon/migrations/versions/20261107_110002_create_project_operating_knowledge_embeddings.sql",
        "daemon/migrations/versions/20261107_110003_add_pok_source_tier.sql",
        "daemon/migrations/versions/20261107_122648_create_source_gc_archive_tables.sql",
        "daemon/models/source.py",
        "daemon/plane_workspace.py",
        "daemon/registry.py",
        "daemon/repositories/anomaly/__init__.py",
        "daemon/repositories/anomaly/models.py",
        "daemon/repositories/anomaly/repository.py",
        "daemon/repositories/memoir/__init__.py",
        "daemon/repositories/memoir/models.py",
        "daemon/repositories/memoir/repository.py",
        "daemon/repositories/memoir/seed.py",
        "daemon/repositories/pok/__init__.py",
        "daemon/repositories/pok/models.py",
        "daemon/repositories/pok/repository.py",
        "daemon/repositories/source/models.py",
        "daemon/repositories/source/repository.py",
        "daemon/routers/__init__.py",
        "daemon/routers/live_views.py",
        "daemon/routers/mcp_servers.py",
        "daemon/routers/nest_reports.py",
        "daemon/routers/schedules.py",
        "daemon/routers/sources.py",
        "daemon/services/anomaly_service.py",
        "daemon/services/context_messages.py",
        "daemon/services/instance_messaging.py",
        "daemon/services/live_views.py",
        "daemon/services/memoir_briefing.py",
        "daemon/services/memoir_embedding_worker.py",
        "daemon/services/memoir_retrieval.py",
        "daemon/services/metrics_query/protocol.py",
        "daemon/services/metrics_query/roster.py",
        "daemon/services/plane_sync_service.py",
        "daemon/services/pok_ari_seam.py",
        "daemon/services/pok_injection.py",
        "daemon/services/pok_service.py",
        "daemon/services/project_skill_source/model.py",
        "daemon/services/prompt_refresh.py",
        "daemon/services/release_manifest.py",
        "daemon/services/scheduling_service.py",
        "daemon/services/skill_injection_service.py",
        "daemon/services/skill_search_service.py",
        "daemon/services/sources_gc_service.py",
        "daemon/services/upgrade_journal_sweep.py",
        "daemon/services/view_passcode_pages.py",
        "daemon/sources/registry.py",
        "daemon/tools/_tool_registry.py",
        # guideline-read-tool lane (2026-11-13): the lane's NEW file —
        # the guidelines privileged-read tool module — recorded per the
        # rule above (THE RECORD IS MAINTAINED BY THE CHANGING LANE).
        "daemon/tools/guidelines.py",
        "daemon/tools/inner_soul.py",
        "daemon/tools/instance.py",
        "daemon/tools/job_queue.py",
        "daemon/tools/mcp_tools.py",
        "daemon/tools/memoir.py",
        "daemon/tools/memoir_knob.py",
        "daemon/tools/metrics_read.py",
        "daemon/tools/nest_report.py",
        "daemon/tools/nest_report_read.py",
        "daemon/tools/plane_tools.py",
        "daemon/tools/pok.py",
        "daemon/tools/prompt_reload.py",
        "daemon/tools/provenance.py",
        "daemon/tools/scheduling.py",
        "daemon/tools/skill_tools.py",
        "daemon/tools/upgrade_journal.py",
        "daemon/tools/upgrade_tools.py",
    }
)


def _assert_lane_allowlist_complete(
    owned: frozenset[str], allowlist: tuple[str, ...]
) -> None:
    """The B-4 assertion — every lane-owned file MUST be allowlisted.

    This is the direction that CAN fail: the owned list and the
    allowlist are independent literals, so dropping a lane file from the
    allowlist (or adding one to the owned list) REDs here.
    """
    missing = sorted(path for path in owned if path not in allowlist)
    assert missing == [], (
        "LANE-CAUSED offenders — a decision-lane file missing from "
        f"ALLOWED_DAEMON_DIFF: {missing}"
    )


def _assert_allowlist_did_not_widen(
    allowlist: tuple[str, ...],
    owned: frozenset[str],
    prior: frozenset[str],
) -> None:
    """Direction 2 — the allowlist may not silently widen."""
    widened = sorted(
        path
        for path in allowlist
        if path not in owned and path not in prior
    )
    assert widened == [], (
        "the allowlist SILENTLY WIDENED — an entry that is neither "
        f"lane-owned nor a frozen prior entry: {widened}"
    )


def _assert_lane_package_files_recorded(
    package_dir: Path, owned: frozenset[str]
) -> None:
    """Direction 3 — every file ON DISK in the lane's daemon package is
    lane-owned.

    The census diff (``git diff --name-only LANE_BASE``) cannot see an
    UNCOMMITTED (untracked) file, and the registered pack's scope is
    ``tests/unit/decision/`` only — so a new artefact under ``daemon/``
    is invisible to both. This sweep closes that gap: it reads the
    directory, not the diff.
    """
    on_disk = {
        str(path.relative_to(package_dir.parents[2]))
        for path in package_dir.iterdir()
        if path.is_file() and not path.name.endswith(".pyc")
    }
    unrecorded = sorted(path for path in on_disk if path not in owned)
    assert unrecorded == [], (
        "a file in the lane's daemon package is not recorded in "
        f"LANE_OWNED_DAEMON — record it (or remove it): {unrecorded}"
    )


def _assert_no_new_daemon_offenders(
    daemon_changes: list[str],
    allowlist: tuple[str, ...],
    pre_existing: frozenset[str],
) -> None:
    """No changed daemon path may be neither allowlisted nor recorded."""
    new_offenders = sorted(
        path
        for path in daemon_changes
        if path not in allowlist and path not in pre_existing
    )
    assert new_offenders == [], (
        f"{new_offenders} is a NEW offender: it is neither allowlisted nor in "
        "the recorded pre-existing set."
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
        assert len(verdicts) == 28
        expected = (
            {"F0.1", "F0.2", "F0.3", "F0.4"}
            | {f"F{n}" for n in range(1, 24)}
            | {"decision_overlay"}
        )
        assert set(verdicts) == expected
        for family, (verdict, seam) in verdicts.items():
            assert verdict in adjudication.VALID_VERDICTS, family
            assert seam and seam.strip(), family

    def test_the_binding_subset_is_eighteen_of_twenty_eight(self) -> None:
        verdicts = adjudication.CATALOG_FAMILY_VERDICTS
        full = [f for f, (v, _s) in verdicts.items() if v == "IN"]
        partial = [f for f, (v, _s) in verdicts.items() if v == "PARTIAL"]
        assert len(full) == 12
        assert len(partial) == 6
        assert tuple(sorted(full + partial)) == tuple(sorted(adjudication.REGISTERED_FAMILIES))

    def test_the_registered_families_match_the_verdicts(self) -> None:
        registered = set(registry.registered_families())
        assert registered == set(adjudication.REGISTERED_FAMILIES)

    def test_every_registered_family_carries_a_band_entry(self) -> None:
        """AM-2(b) — the band-coverage pin.

        The band check (:meth:`TestTheBudget.
        test_each_family_stays_inside_its_declared_band`) iterates
        ``FAMILY_SERIES_BANDS``, so a registered family with NO band
        entry is SILENTLY SKIPPED — the family would carry no closed-
        space cap at all.  This pin makes the omission loud.
        """
        missing = set(adjudication.REGISTERED_FAMILIES) - set(
            adjudication.FAMILY_SERIES_BANDS
        )
        assert not missing, sorted(missing)


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
        # B-4 — TWO INDEPENDENT ASSERTIONS, both able to fail:
        #   (1) every lane-owned file is allowlisted (a lane file dropped
        #       from the allowlist REDs);
        #   (2) no changed daemon path is neither allowlisted nor in the
        #       recorded pre-existing set (a NEW offender REDs).
        _assert_lane_allowlist_complete(LANE_OWNED_DAEMON, ALLOWED_DAEMON_DIFF)
        _assert_no_new_daemon_offenders(
            daemon_changes, ALLOWED_DAEMON_DIFF, PRE_EXISTING_DAEMON_OFFENDERS
        )

    def test_the_lane_census_is_consistent_in_both_directions(self) -> None:
        """B-4+ — the BOTH-DIRECTIONS consistency pin.

        Direction 1 — every lane-owned path is allowlisted (a lane file
        dropped from the allowlist REDs).

        Direction 2 — the allowlist never SILENTLY WIDENS: every entry
        is either lane-owned or one of the FROZEN prior entries (the
        metrics-query lane's own surfaces). An allowlist entry that is
        neither REDs.

        Direction 3 — THE ARTEFACT CLASS: every file ON DISK in the
        lane's own daemon package is lane-owned. This is the leg that
        catches a new lane artefact — INCLUDING AN UNTRACKED ONE (the
        census diff cannot see an uncommitted file, and the registered
        pack's scope is ``tests/unit/decision/`` only, so a new file
        under ``daemon/`` is invisible to both). The artefact that
        caused the measured red — ``MUTATION_MATRIX.md`` — is exactly
        this class.
        """
        # Direction 1
        _assert_lane_allowlist_complete(LANE_OWNED_DAEMON, ALLOWED_DAEMON_DIFF)
        # Direction 2
        _assert_allowlist_did_not_widen(
            ALLOWED_DAEMON_DIFF, LANE_OWNED_DAEMON, PRIOR_ALLOWLIST_ENTRIES
        )
        # Direction 3 — the on-disk sweep (catches an untracked artefact)
        _assert_lane_package_files_recorded(
            Path(__file__).resolve().parents[3] / "daemon" / "services" / "decision",
            LANE_OWNED_DAEMON,
        )

    def test_the_daemon_diff_pin_is_discriminating(self) -> None:
        """B-4 — the rewritten pin CAN fail (in-memory proof).

        The previous shape derived the allowlist from the owned list, so
        its "no lane-caused offender" assertion was incapable of failing
        — the conclusion it claimed was established by the tester's HAND
        verification, not by the pin. This test proves the REWRITTEN pin
        is discriminating: with one lane-owned file removed from the
        allowlist (in memory — no git, no tree write), the assertion
        RAISES. The same holds for a new, unrecorded offender.
        """
        import pytest as _pytest

        doctored = tuple(
            path
            for path in ALLOWED_DAEMON_DIFF
            if path != "daemon/services/decision/overlay.py"
        )
        with _pytest.raises(AssertionError) as excinfo:
            _assert_lane_allowlist_complete(LANE_OWNED_DAEMON, doctored)
        assert "daemon/services/decision/overlay.py" in str(excinfo.value)

        with _pytest.raises(AssertionError) as excinfo:
            _assert_no_new_daemon_offenders(
                ["daemon/services/decision/brand_new_file.py"],
                ALLOWED_DAEMON_DIFF,
                PRE_EXISTING_DAEMON_OFFENDERS,
            )
        assert "brand_new_file.py" in str(excinfo.value)

        # Direction 2 — a SILENTLY WIDENED allowlist REDs (an entry that
        # is neither lane-owned nor a frozen prior entry).
        with _pytest.raises(AssertionError) as excinfo:
            _assert_allowlist_did_not_widen(
                ALLOWED_DAEMON_DIFF + ("daemon/services/decision/sneaky.py",),
                LANE_OWNED_DAEMON,
                PRIOR_ALLOWLIST_ENTRIES,
            )
        assert "sneaky.py" in str(excinfo.value)

        # Direction 3 — the ON-DISK sweep: a file that exists in the
        # lane's package but is NOT recorded REDs (the measured class:
        # ``MUTATION_MATRIX.md`` was invisible to the diff-based census
        # until it was recorded). Proven IN MEMORY — the owned set is
        # doctored, no file is created and no tree is written.
        package_dir = (
            Path(__file__).resolve().parents[3] / "daemon" / "services" / "decision"
        )
        with _pytest.raises(AssertionError) as excinfo:
            _assert_lane_package_files_recorded(
                package_dir,
                frozenset(
                    path
                    for path in LANE_OWNED_DAEMON
                    if path != "daemon/services/decision/MUTATION_MATRIX.md"
                ),
            )
        assert "MUTATION_MATRIX.md" in str(excinfo.value)

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
    def test_the_vocabulary_version_moved_only_for_the_tool_category_axis(self) -> None:
        """THE MARKER MOVED — ON THE TOOL-CATEGORY AXIS ONLY.

        The original finding (P4) required NO further vocabulary bump:
        the P1 contract freezes ``vocabulary_version: 2`` ("2 after this
        lane") and P2's success criterion was ``VOCABULARY_VERSION == 2``
        — Stage A delivered that bump (the F7 op narrowing), and P4's
        new families' label vocabularies are part of the SAME frozen
        annex (the D5 F18 row puts the amended audit vocabulary on the
        1→2 bump).

        The marker has since moved 2 → 3 — for a DIFFERENT vocabulary:
        the TOOL-CATEGORY label set (the metrics-agent-tools lane
        registered the ``metrics-read`` privileged category, so the
        frozen label set changed and the bump contract requires the
        marker to move) — 3 → 4 for the SAME axis again (the
        nest-anomaly-report lane registered the ``nest-report`` WRITE
        category) — 4 → 5 for the SAME axis a second time (that lane's
        Phase 3 registered the ``nest-report-read`` PRIVILEGED READ
        category) — 5 → 6 for the SAME axis a third time (the
        memoir-store-v1 lane registered the ``memoir`` privileged
        category (#19)) — and 6 → 7 for the SAME axis a fourth time
        (the prompt-reload lane registered the ``prompt-reload``
        PRIVILEGED category (#20, ari-exactly); four separate lane
        commits, never one jump).  The F7 op enum's own annex
        (``{aget, other}``)
        did NOT move, and the op-enum pins in this file still hold.
        """
        # "pok" re-point 2026-11-08 (POK Phase 1 F-1 ceremony, commit
        # 16b4e70b2): the sanctioned 7 -> 8 bump; this file's op-enum
        # annex pins are untouched.
        # prompt-assent 2026-11-10 (Phase 2): the sanctioned 8 -> 9 bump
        # (the ``prompt-assent`` PRIVILEGED category, #21, joined the
        # tool-category label set — the SAME axis, a fifth time).
        # guidelines 2026-11-13 (guideline-read-tool lane): the
        # sanctioned 9 -> 10 bump (the ``guidelines`` PRIVILEGED READ
        # category, #22 — the first privileged grant to a sonic agent,
        # sonic-leader — joined the tool-category label set; the SAME
        # axis, a sixth time).
        assert cm.VOCABULARY_VERSION == 10

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
