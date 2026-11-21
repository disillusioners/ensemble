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
        # agent group chat P6 lane (2026-11-16): the SSE room-resolution
        # pre-branch rode the events route (the daemon path this lane
        # changed).
        "daemon/routers/messages.py",
        "daemon/routers/mcp_servers.py",
        "daemon/routers/nest_reports.py",
        "daemon/routers/schedules.py",
        "daemon/routers/sources.py",
        "daemon/services/anomaly_service.py",
        "daemon/services/context_block.py",
        "daemon/services/context_messages.py",
        "daemon/services/instance_messaging.py",
        # agent group chat P6 lane (2026-11-16): the H2 extraction's two
        # new modules (the shared block primitives + the joined-room
        # block) and the hub's room-resolution alias registration.
        "daemon/services/joined_room_context.py",
        "daemon/services/live_event_hub.py",
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
        # agent group chat P4 lane (2026-11-15/16): the nudge
        # coordinator. Registered per the P4 tester gate's RED-4
        # disposition (2026-11-16, "add daemon/services/room_nudge.py +
        # daemon/tools/todo_tools.py to PRE_EXISTING_DAEMON_OFFENDERS") —
        # the offender stays NAMED and attributable instead of surfacing
        # as a new offender in a later lane's red.
        "daemon/services/room_nudge.py",
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
        # agent group chat P6 lane (2026-11-16): the room tool surface
        # (the H1 gate extraction + the P6 backlog fixes).
        "daemon/tools/room.py",
        "daemon/tools/scheduling.py",
        "daemon/tools/skill_tools.py",
        # agent group chat P4 lane (2026-11-15/16): the todo-tool
        # reminder carriers (the P4 nudge carriers ride the reminder
        # return). Registered with the P4 nudge coordinator above —
        # the P4 tester gate's RED-4 disposition (2026-11-16).
        "daemon/tools/todo_tools.py",
        "daemon/tools/upgrade_journal.py",
        "daemon/tools/upgrade_tools.py",
        # ── Tool-usage-metrics P1 lane (2026-11-21) — council C3 ─────
        # THE RECORD IS MAINTAINED BY THE CHANGING LANE (rule above):
        # these NINE paths are this lane's own daemon surfaces, each
        # attributed to the phase commit that OWNS it, so the offender
        # stays NAMED and attributable instead of surfacing as a new
        # offender in a later lane's red.
        #
        # phase 1 — the daily ledger, the observation tap, the flusher,
        # the migration and the API door (owning commit 49f5e9c1d):
        "daemon/migrations/versions/20261121_020000_create_tool_usage_daily.sql",
        "daemon/repositories/tool_usage/__init__.py",
        "daemon/repositories/tool_usage/models.py",
        "daemon/repositories/tool_usage/repository.py",
        "daemon/routers/tool_usage.py",
        "daemon/services/tool_usage_observation.py",
        # phase 3 — the deterministic digest and the boot-time seeder
        # (owning commit 71273d7e3):
        "daemon/repositories/source/seed.py",
        "daemon/services/tool_usage_digest.py",
        "daemon/tools/tool_usage.py",
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

    def test_the_measured_total_is_625_under_the_cap(self) -> None:
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
        """
        wiring_metrics = [
            m
            for m in registry.registered_family_metrics()
            if m.family != "decision_overlay"
        ]
        measured = _closed_label_space(wiring_metrics)
        total = adjudication.BASELINE_SERIES + measured
        assert total == 625
        assert total <= adjudication.GLOBAL_SERIES_CAP


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
