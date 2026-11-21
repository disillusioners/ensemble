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
from daemon.services import metrics_gate
from daemon.services.metrics_query.wiring import adjudication
from daemon.services.metrics_query.wiring import registry
from daemon.services.metrics_query.wiring import sources as wiring_sources
from daemon.services.metrics_query.wiring import vocab
from daemon.services.metrics_query.wiring import w5
from daemon.services.metrics_query.wiring import ws_a

# The Decision Module P1 lane's family (``decision_overlay``) is
# registered by ITS OWN module — the census's other families live
# inside the wiring package, this one does not — so the pack loads it
# here, exactly as it loads ``ws_a``/``vocab``/``sources``.  Without
# this import the family would be absent from the registry and the
# census would (correctly) report a mismatch.
from daemon.services.decision import metrics as decision_metrics  # noqa: F401

#: THIS LANE's TRUE base commit — the diff-review pin's anchor.
#:
#: RE-POINTED (tester gate, 2026-11-21) from the metrics-query lane's
#: P4-stage base ``644c2bfaa`` (the anchor this file was authored
#: against) to the commit the tool-usage-metrics-p1 branch was actually
#: cut from. The old anchor predates EVERY other lane's landings, so
#: ``git diff 644c2bfaa --name-only`` returned 308 changed daemon paths
#: — 295 of them pin-visible and NONE of them this lane's — and the pin
#: could never discriminate: it reddened on 157 inherited foreign
#: paths and stayed red no matter what this lane did. Against the true
#: base the same query returns 21 daemon paths (17 pin-visible, 4 under
#: ``WIRING_ROOT``), EVERY ONE of them this lane's own surface, so the
#: allowlist below is exactly this lane's daemon footprint and a single
#: foreign path reddens the pin.
#:
#: Measured (script, not hand-typed — see ``PRE_EXISTING_DAEMON_OFFENDERS``):
#:   old base 644c2bfaa: changed=308  pin-visible=295  offenders=157
#:   true base 0b5624dc9: changed=21  pin-visible=17   offenders=0
LANE_BASE = "0b5624dc9d57fe1c8ec49415382cacb2cbd55f25"

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
        # tool-usage-metrics-p1 lane (49f5e9c1d): the observation-wrapper
        # CALL SITE — a lane surface, so it is owned here as well as
        # allowlisted below (direction 2 treats a new ALLOWED entry that
        # is not lane-owned as a SILENT WIDENING).
        "daemon/graph.py",
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
    # ── Tool-usage-metrics P1 lane (2026-11-21) — council C3 follow-up ──
    # daemon/graph.py: the observation-wrapper CALL SITE in
    # build_instance_graph (lane commit 49f5e9c1d) — it wraps
    # wrapped_tools_node between compact_note and the ToolNode so the tap
    # sees the result to set the ok flag. A CALL-SITE surface only: no
    # observe/inc call is added here (the pin's protection target is the
    # producers, and every producer path stays outside this list).
    "daemon/graph.py",
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


#: The daemon paths that are RECORDED as known non-allowlisted — i.e.
#: the residual set the pin names instead of failing on. Historically
#: this literal meant "paths that were ALREADY offenders when the base
#: was cut (other lanes' committed work)"; after the re-point below it
#: holds THIS lane's residual tail and NO foreign rows. The pin names
#: the whole set in its failure message; it never silently absorbs an
#: unknown path.
#:
#: **THE RECORD IS MAINTAINED BY THE CHANGING LANE.** A lane that
#: changes daemon files records them here in its own change, so the
#: offender stays NAMED and attributable instead of showing up as a
#: new offender in a later lane's red.
#:
#: RE-POINTED + REGENERATED (tester gate, 2026-11-21). The original
#: literal accumulated one row per lane since the metrics-query P4 base
#: (108 rows; it could not distinguish "inherited" from "ours"). Now
#: that ``LANE_BASE`` is this lane's TRUE base, the inherited set is
#: EMPTY — nothing but this lane's own work landed in the span — so the
#: set below is the MECHANICALLY GENERATED complement of the lane's own
#: footprint: every daemon path the lane changed (``git diff --name-only
#: <LANE_BASE>..HEAD -- daemon/``) that is neither in
#: ``ALLOWED_DAEMON_DIFF`` nor under ``WIRING_ROOT``. There are NO
#: foreign rows left: the residual rows are this lane's OWN unallowlisted
#: surfaces, and they are recorded here rather than "cured" (the pin's
#: design intent — every lane's tail is named and attributable, never
#: silently absorbed).
#:
#: READ THIS BEFORE TRUSTING THE ZERO. Because the rows and the diff come
#: from the SAME generator, "zero offenders against LANE_BASE" is true BY
#: CONSTRUCTION for these 12 paths — the set is a RECORD of this lane's
#: tail, not evidence of clean history. The pin's teeth are on the OTHER
#: direction: a daemon path that enters the diff after this recording (a
#: later lane's file, a stray edit) is in neither literal and REDs. That
#: is the discrimination the old 108-row literal destroyed (it carried
#: five lanes of foreign rows, so everything reddened and nothing
#: distinguished).
#:
#: Generator (committed command; rows are never hand-typed):
#:   python3 scripts/gen_tool_usage_daemon_allowlist.py \
#:       --base 0b5624dc9d57fe1c8ec49415382cacb2cbd55f25 \
#:       --test-file tests/unit/metrics/test_metrics_family_adjudication.py \
#:       --wiring-root daemon/services/metrics_query/wiring
PRE_EXISTING_DAEMON_OFFENDERS = frozenset(
    {
        "daemon/migrations/versions/20261121_020000_create_tool_usage_daily.sql",  # 49f5e9c1d
        "daemon/repositories/source/seed.py",  # 0b02931b7
        "daemon/repositories/tool_usage/__init__.py",  # 49f5e9c1d
        "daemon/repositories/tool_usage/models.py",  # 49f5e9c1d
        "daemon/repositories/tool_usage/repository.py",  # 1aa360051
        "daemon/routers/__init__.py",  # 49f5e9c1d
        "daemon/routers/tool_usage.py",  # 0b02931b7
        "daemon/services/tool_usage_digest.py",  # 1aa360051
        "daemon/services/tool_usage_observation.py",  # 0b02931b7
        "daemon/tools/_tool_registry.py",  # 8b8df3ead
        "daemon/tools/instance.py",  # 0b02931b7
        "daemon/tools/tool_usage.py",  # 1aa360051
        # ── THE ENGINE-ARC P1-P6 PROGRAM — T6.3 census ceremony union (2026-11-21) ──
        # MECHANICALLY GENERATED: `git diff --name-only <LANE_BASE>` daemon paths
        # not in ALLOWED_DAEMON_DIFF / this set, plus this commit's own two
        # new files. THE RECORD IS MAINTAINED BY THE CHANGING LANE — the
        # engine-arc program is that lane for every row below.
        #
        # P7 INTAKE (one lookup; DO NOT implement here):
        #  * QUARANTINE ledger corrections — the kwarg-drift family attribution
        #    (test_child_report_next_turn_execution family + the queue_routing
        #    red) and a metrics-confinement offender-list enlargement row.
        #  * A function-scoped create_event pin (the P5 event-registry surface).
        #  * Per-site census pins for the multi-site kinds (room_delivery x7,
        #    midflight_qa x4, job_completed x2, error x2).
        #  * Two regex tightenings: the census alias form + the D5.11
        #    from-import form.
        #  * The D5.4 SECOND live source (beyond EventBus._global_subscribers).
        "daemon/__init__.py",
        "daemon/migrations/versions/20261121_120000_create_engine_jobs.sql",
        "daemon/migrations/versions/20261121_120001_create_engine_runs.sql",
        "daemon/plugin_subsystem/config_compat_contract.py",
        "daemon/repositories/bash_spawn/models.py",
        "daemon/repositories/bash_spawn/repository.py",
        "daemon/repositories/engine/__init__.py",
        "daemon/repositories/engine/models.py",
        "daemon/repositories/engine/repository.py",
        "daemon/repositories/room/membership.py",
        "daemon/repositories/room/repository.py",
        "daemon/routers/answer_helper.py",
        "daemon/routers/engine.py",
        "daemon/routers/instances.py",
        "daemon/routers/schedules.py",
        "daemon/services/bash_spawn_reaper.py",
        "daemon/services/child_reports.py",
        "daemon/services/engine/__init__.py",
        "daemon/services/engine/dispatch.py",
        "daemon/services/engine/dualpath.py",
        "daemon/services/engine/engine.py",
        "daemon/services/engine/estate.py",
        "daemon/services/engine/execution.py",
        "daemon/services/engine/guardrails.py",
        "daemon/services/engine/killswitch.py",
        "daemon/services/engine/locks.py",
        "daemon/services/engine/metrics.py",
        "daemon/services/engine/projection.py",
        "daemon/services/engine/boot.py",  # P4 (924820dc0)
        "daemon/services/engine/watchdog.py",  # P4 (924820dc0)
        "daemon/services/engine/registry.py",
        "daemon/services/engine/spec.py",
        "daemon/services/engine/state.py",
        "daemon/services/event_publisher.py",
        "daemon/services/event_registry/__init__.py",
        "daemon/services/event_registry/catalog.py",
        "daemon/services/event_registry/emit.py",
        "daemon/services/event_registry/metrics.py",
        "daemon/services/instance_messaging.py",
        "daemon/services/job_feedback_observer.py",
        "daemon/services/job_recovery_service.py",
        "daemon/services/message_processing_errors.py",
        "daemon/services/midflight_qa.py",
        "daemon/services/orphan_watcher_sweep.py",
        "daemon/services/ram_guard/guard.py",
        "daemon/services/ram_guard_events.py",
        "daemon/services/report_delivery_recovery.py",
        "daemon/services/room_delivery.py",
        "daemon/services/schedule_fire_notify.py",
        "daemon/services/schedule_fire_notify_metrics.py",
        "daemon/services/scheduling_service.py",
        "daemon/services/stale_task_recovery.py",
        "daemon/sources/adapters/scheduler.py",
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
        assert len(verdicts) == 29
        expected = (
            {"F0.1", "F0.2", "F0.3", "F0.4"}
            | {f"F{n}" for n in range(1, 25)}
            | {"decision_overlay"}
        )
        assert set(verdicts) == expected
        for family, (verdict, seam) in verdicts.items():
            assert verdict in adjudication.VALID_VERDICTS, family
            assert seam and seam.strip(), family

    def test_the_binding_subset_is_nineteen_of_twenty_nine(self) -> None:
        verdicts = adjudication.CATALOG_FAMILY_VERDICTS
        full = [f for f, (v, _s) in verdicts.items() if v == "IN"]
        partial = [f for f, (v, _s) in verdicts.items() if v == "PARTIAL"]
        assert len(full) == 13
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
    #: THE FOOTPRINT ARITHMETIC — what each count means for the pin.
    #:
    #: Two counts read the same diff and answer DIFFERENT questions, and
    #: conflating them is how the anchor drift went unnoticed for five
    #: lanes (a "58-hunks" figure was carried around beside a "13-paths"
    #: figure as if they described one surface):
    #:
    #:   * PATHS (``git diff --name-only``) — the allowlist's unit. The
    #:     pin is a PATH-level gate: a path is allowlisted, recorded, or
    #:     an offender. Adding lines to an allowlisted file does not
    #:     change the verdict; a NEW path does.
    #:   * HUNKS (``git diff -U0 | grep -c '^@@'``) — the review's unit.
    #:     A hunk count says how much churn a path carries; it never
    #:     enters the assertion. Marker completeness (the source-gc
    #:     guard in tests/unit/repositories/test_source_gc_repository.py)
    #:     is the mechanism that judges hunks — and only for
    #:     daemon/manager.py + daemon/constants.py, not for this pin.
    #:
    #: MEASURED at HEAD 0b02931b7 against LANE_BASE 0b5624dc9
    #: (``-U0``; the default ``-U3`` merges some adjacent hunks and
    #: yields 35 / 25):
    #:   whole lane, ``daemon/``:        39 hunks across 21 paths
    #:   pin-visible (not WIRING_ROOT):  25 hunks across 17 paths
    #:   under WIRING_ROOT (excluded):   14 hunks across  4 paths
    #:   recorded in PRE_EXISTING_...:  the lane's own 12 residual paths
    #: (Those 12 paths ARE pin-visible work — the set records them as
    #: the lane's own complement because they are not in
    #: ALLOWED_DAEMON_DIFF; see its docstring. The remaining 5
    #: pin-visible paths are the allowlisted-and-lane-owned five:
    #: api.py, config.py, graph.py, manager.py, settings_service.py.)
    #:
    #: ON THE REVIEW'S "58 HUNKS / 13 PATHS": those are NOT this pin's
    #: daemon numbers (daemon measures 39 hunks across 21 paths here).
    #: They match the lane's FRONTEND surface exactly — 13 = the lane's
    #: FE paths (the tool-usage block of
    #: FRONTEND_UI_LANE_SANCTIONED_FILES in
    #: tests/unit/repositories/test_source_gc_repository.py), 58 = the
    #: FE hunks that surface's tree-level compensator counts as
    #: OFFENDING against its fixed base 858a85e31 (122 FE hunks total, 64
    #: of them inside the allowlist). Neither number enters THIS pin; if
    #: the review meant the daemon footprint, its figures are stale and
    #: the measured column above is the correction.
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
        # room 2026-11-16 (group-chat lane): the sanctioned 10 -> 11
        # bump (the ``room`` PRIVILEGED category, #23 — the agent
        # group-chat surface — joined the tool-category label set; the
        # SAME axis, a seventh time).
        assert cm.VOCABULARY_VERSION == 11

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


class TestTheF24Family:
    """The tool-usage family (the tool-usage-metrics-p1 lane, Phase 2).

    T1/T5/T6 acceptance — the family's registration structure, its
    closed label space, its band, and the measured-total convention pin
    (decisions.md D18 / approver fix B5: 491 + 134 = 625 ≤ 2000 is THE
    claim, measured convention; the plan's ≈1095 is a band-sum and is
    never asserted here).
    """

    def test_f24_is_registered_with_its_spec(self) -> None:
        spec = registry.registered_families()["F24"]
        assert spec.cluster == "W6"
        assert spec.verdict == "IN"
        assert spec.metrics == ("ensemble_tool_usage_calls_total",)
        verdict, seam = adjudication.CATALOG_FAMILY_VERDICTS["F24"]
        assert verdict == "IN"
        assert "w5.py" in seam and seam.strip()

    def test_the_f24_metric_declares_the_closed_label_space(self) -> None:
        metric = next(
            m
            for m in registry.registered_family_metrics()
            if m.name == "ensemble_tool_usage_calls_total"
        )
        assert metric.family == "F24"
        assert metric.kind == "counter"
        assert metric.label_keys == ("tool_category", "result")
        assert set(metric.allowed_values["tool_category"]) == set(
            cm.tool_category_vocabulary()
        )
        # 66 committed categories + the reserved `other` = 67.
        assert len(metric.allowed_values["tool_category"]) == 67
        assert metric.allowed_values["result"] == frozenset({"ok", "error"})

    def test_the_f24_registration_is_am3_complete(self) -> None:
        metric = next(
            m
            for m in registry.registered_family_metrics()
            if m.name == "ensemble_tool_usage_calls_total"
        )
        assert metric.source_idiom == "registry"
        assert metric.durability == "process"
        assert isinstance(metric.cardinality_weight, int)

    def test_the_f24_name_matches_the_name_rule(self) -> None:
        """`^ensemble_[a-z0-9_]+$` (checkpoint_metrics.py:110)."""
        import re

        assert re.fullmatch(r"ensemble_[a-z0-9_]+", "ensemble_tool_usage_calls_total")

    def test_f24_is_distinct_from_the_f20_out_name(self) -> None:
        """F20's OUT `ensemble_tool_calls_total` stays absent; F24's name is its own."""
        names = {m.name for m in cm.registered_metrics()}
        assert "ensemble_tool_usage_calls_total" in names
        assert "ensemble_tool_calls_total" not in names

    def test_the_f24_band_is_134_plus_26_headroom(self) -> None:
        assert adjudication.FAMILY_SERIES_BANDS["F24"] == 160
        family_metrics = [
            m
            for m in registry.registered_family_metrics()
            if m.family == "F24"
        ]
        measured = _closed_label_space(family_metrics)
        assert measured == 134
        assert measured <= adjudication.FAMILY_SERIES_BANDS["F24"]

    #: THE FROZEN RELEASE-VERSION SNAPSHOT the total is measured against.
    #:
    #: ``vocab.RELEASE_VERSIONS`` is HOST-COUPLED BY DESIGN (vocab.py
    #: ``_release_versions`` scans ``~/agents-ensemble{,-demo}/releases/*``
    #: at import time), so every release dir staged or evicted moves the
    #: ``version`` label's cardinality by one and shifts ``measured`` by
    #: the same amount — measured 535↔536, total 624↔625 (observed
    #: 2026-11-21: 624 before v0.31.24 was staged, 625 after). That is a
    #: HOST-STATE signal, not a family-count regression, and this pin is
    #: about the family count (D18: 89 + 536 = 625 ≤ 2000). So the
    #: ``version`` label's VALUE SET is frozen here to the 10 names the
    #: claim was measured against (``other`` + 9 scanned dir names, one of
    #: which is a stray non-release directory — kept verbatim below).
    #: The host's REAL cardinality stays bounded by
    #: ``test_the_f21_release_vocabulary_is_bounded`` (≤ 32) and must
    #: contain ``RESERVED`` — that pair is what keeps this freeze honest:
    #: the label is still a closed set with a fold target, only its host
    #: size stops entering the arithmetic.
    FROZEN_RELEASE_VERSIONS = frozenset(
        {
            "other",
            "v0.16.0",
            "v0.16.2-demo-e2e",
            "v0.16.3",
            "v0.28.15",
            "v0.31.21",
            "v0.31.22",
            "v0.31.23",
            "v0.31.24",
            # The install's stray non-release dir that the scan picks up
            # (``releases/rollback.lock.d.stale.swept-635423``) — kept
            # VERBATIM so the snapshot is the OBSERVED set, not an
            # idealized one. It is what the 625 was measured against.
            "rollback.lock.d.stale.swept-635423",
        }
    )

    def test_the_measured_total_is_625_under_the_cap(self, monkeypatch) -> None:
        """THE claim (D18 / B5) — 89 baseline + 536 measured = 625 ≤ 2000.

        THE CONVENTION IS EXPLICIT (D18's correction arc): the claim's
        helper measures the WIRING-registered families — the decision
        overlay's own family is NOT in the set (it is registered by
        ``daemon/services/decision/metrics.py`` and rides the pack's
        enforced cumulative instead: 89 + 813 + 134 = 1036 ≤ 2000,
        covered by ``test_the_cumulative_count_stays_inside_the_global_
        cap``). 491 today (89 + 402) + 134 (F24) = 625.  The == pin is
        the convention pin: a future family that moves the wiring
        closed space must consciously re-pin this number (the
        predicate-ledger discipline) — a silent drift REDs here.

        HOST-INDEPENDENCE (tester gate 2026-11-21): the one host-coupled
        label — F21's ``version``, whose value set is the install's
        release-dir scan — is frozen to ``FROZEN_RELEASE_VERSIONS`` for
        the duration of the measurement, so the total depends on the
        FAMILY SHAPE and not on which releases this host happens to have
        staged. Without the freeze this tree measured 624 on 2026-11-20
        and 625 on 2026-11-21 (v0.31.24 was staged in between), and under
        a synthetic HOME holding ONE release dir the UNFROZEN pin reads
        618 on the SAME tree — the pin was a host clock, not a
        regression gate. Under the freeze all three read 625.
        """
        version_metric = next(
            m for m in registry.registered_family_metrics() if "version" in m.label_keys
        )
        # The freeze targets ONE metric: if a second version-labelled
        # metric ever registers, this pin must be re-read (the "first
        # match" would silently freeze the wrong one).
        assert [
            m.name for m in registry.registered_family_metrics() if "version" in m.label_keys
        ] == [version_metric.name]
        # Same object, same dict: only the value SET is swapped, so the
        # label key, the metric identity and the counting convention are
        # untouched (monkeypatch restores the dict entry afterwards).
        monkeypatch.setitem(
            version_metric.allowed_values, "version", self.FROZEN_RELEASE_VERSIONS
        )
        wiring_metrics = [
            m
            for m in registry.registered_family_metrics()
            if m.family != "decision_overlay"
        ]
        measured = _closed_label_space(wiring_metrics)
        total = adjudication.BASELINE_SERIES + measured
        assert total == 625
        assert total <= adjudication.GLOBAL_SERIES_CAP
        # The freeze is cardinality-faithful: F21's release label still
        # contributes exactly the 10 series the claim counted.
        assert len(self.FROZEN_RELEASE_VERSIONS) == 10


class TestTheF24FamilyGate:
    """T3 — the kill-switch matrix (fail-closed, stacked, independent).

    The gate is PURE on the read path: no DB round trip ever happens
    (the managed tiers are the cached live values; a provider installed
    here RAISES if consulted, so a hidden read turns the matrix RED).
    """

    @pytest.fixture(autouse=True)
    def _gate_state(self, monkeypatch):
        monkeypatch.delenv(w5.FAMILY_ENV_VAR, raising=False)
        monkeypatch.delenv(metrics_gate.ENV_VAR, raising=False)
        metrics_gate.reset_for_tests()

        def _no_db_read() -> dict[str, str]:
            raise AssertionError("the gate read path touched the DB tier")

        metrics_gate.set_managed_metrics_provider(_no_db_read)
        yield
        metrics_gate.reset_for_tests()

    def test_env_explicit_zero_is_off_even_with_the_global_gate_on(self, monkeypatch) -> None:
        metrics_gate.set_managed_metrics_enabled(True)  # global ON (boot tier)
        monkeypatch.setenv(w5.FAMILY_ENV_VAR, "0")
        assert w5.family_gate_enabled() is False

    def test_env_explicit_garbage_fails_closed(self, monkeypatch) -> None:
        metrics_gate.set_managed_metrics_enabled(True)
        monkeypatch.setenv(w5.FAMILY_ENV_VAR, "off")  # NOT in {1,true,yes}
        assert w5.family_gate_enabled() is False

    def test_env_unset_with_the_managed_default_is_on_when_global_is_on(self) -> None:
        metrics_gate.set_managed_metrics_enabled(True)
        assert w5.family_gate_enabled() is True

    def test_the_global_gate_off_suppresses_regardless_of_the_family_key(self, monkeypatch) -> None:
        metrics_gate.set_managed_metrics_enabled(False)  # global managed OFF
        monkeypatch.delenv(w5.FAMILY_ENV_VAR, raising=False)
        assert w5.family_gate_enabled() is False
        monkeypatch.setenv(w5.FAMILY_ENV_VAR, "1")  # family explicitly ON
        assert w5.family_gate_enabled() is False

    def test_the_global_env_off_suppresses_regardless_of_the_family_key(self, monkeypatch) -> None:
        metrics_gate.set_managed_metrics_enabled(True)
        monkeypatch.setenv(metrics_gate.ENV_VAR, "0")  # the GLOBAL env off-switch
        monkeypatch.setenv(w5.FAMILY_ENV_VAR, "1")
        assert w5.family_gate_enabled() is False

    def test_the_gate_never_touches_the_db_tier(self) -> None:
        """live_managed_value + metrics_enabled are PURE cached reads."""
        metrics_gate.set_managed_metrics_enabled(True)
        # The provider (installed by the fixture) raises on ANY call.
        assert w5.family_gate_enabled() is True

    def test_the_family_gate_is_independent_of_the_ledger_switch(self, monkeypatch) -> None:
        """Family OFF (env) while the phase-1 ledger switch stays untouched."""
        from daemon.services.tool_usage_observation import is_collection_enabled

        monkeypatch.setenv(w5.FAMILY_ENV_VAR, "0")
        assert w5.family_gate_enabled() is False
        # The ledger's own kill-switch (D7) is a DIFFERENT key + env var.
        monkeypatch.delenv("LIMITS_TOOL_USAGE_COLLECTION_ENABLED", raising=False)
        assert is_collection_enabled() is True


class TestTheF24Sink:
    """T1/T3 — the write-time sink's behavior against the frozen surface."""

    @pytest.fixture(autouse=True)
    def _sink_state(self, monkeypatch):
        monkeypatch.delenv(w5.FAMILY_ENV_VAR, raising=False)
        monkeypatch.delenv(metrics_gate.ENV_VAR, raising=False)
        metrics_gate.reset_for_tests()
        metrics_gate.set_managed_metrics_enabled(True)  # global ON
        w5.tool_usage_calls_total.reset()
        yield
        w5.tool_usage_calls_total.reset()
        metrics_gate.reset_for_tests()

    def _metric(self) -> cm.Metric:
        return next(
            m
            for m in registry.registered_family_metrics()
            if m.name == "ensemble_tool_usage_calls_total"
        )

    def test_the_sink_is_registered_against_the_observer(self) -> None:
        from daemon.services.tool_usage_observation import _snapshot_sinks

        assert w5.on_tool_usage_event in _snapshot_sinks()

    def test_the_sink_does_not_increment_while_the_family_gate_is_off(self, monkeypatch) -> None:
        monkeypatch.setenv(w5.FAMILY_ENV_VAR, "0")
        w5.on_tool_usage_event("bash", "bash", "agent", "proj", True)
        assert (
            self._metric().get(labels={"tool_category": "bash", "result": "ok"}) == 0
        )

    def test_the_sink_increments_when_the_stack_is_on(self) -> None:
        w5.on_tool_usage_event("bash", "bash", "agent", "proj", True)
        w5.on_tool_usage_event("bash", "bash", "agent", "proj", False)
        metric = self._metric()
        assert metric.get(labels={"tool_category": "bash", "result": "ok"}) == 1
        assert metric.get(labels={"tool_category": "bash", "result": "error"}) == 1

    def test_the_sink_folds_none_to_other_without_counting_drift(self) -> None:
        """The unmapped case is steady state: `other`, NOT an invalid label."""
        metric = self._metric()
        before = metric.get(labels={"tool_category": "other", "result": "ok"})
        w5.on_tool_usage_event("mystery_tool", None, "agent", "proj", True)
        assert (
            metric.get(labels={"tool_category": "other", "result": "ok"}) == before + 1
        )

    def test_the_sink_passes_out_of_set_values_raw_for_the_drift_signal(self) -> None:
        """AM-2d — the REGISTRY folds a disallowed value and COUNTS it."""
        metric = self._metric()
        w5.on_tool_usage_event("x", "not-a-category", "agent", "proj", True)
        assert (
            metric.get(labels={"tool_category": "other", "result": "ok"}) == 1
        )
        invalid = cm._invalid_metric
        assert invalid is not None
        assert (
            invalid.get(
                labels={
                    "metric": "ensemble_tool_usage_calls_total",
                    "label": "tool_category",
                }
            )
            >= 1
        )
